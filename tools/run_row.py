"""Run one specific sheet row through the live pipeline, on purpose.

The poller only ever starts rows it has never seen. That is the backfill guard
doing its job, and it means a row already on the sheet — a demonstration, or a
row being re-run after a fix — can never be started by waiting. This starts
exactly one, by tab and row number, through the same `Runner` the poller uses.

Run it **on the droplet, as the `gable` user**, so the run lands in the same
database the Slack listener reads. A run started anywhere else is invisible to
the photo handoff, and Carmen's upload has nothing to attach itself to:

    sudo -u gable /opt/gable/.venv/bin/python -m tools.run_row Testing_1 78

Posts to `GABLE_SLACK_CHANNEL_ID` and nowhere else. Does not handle: rows whose
identity already has three attempts recorded, which the runner refuses by
design.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Final

from gable.agents.contacts import sync_contacts
from gable.config import ConfigError, Settings
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.logging_setup import configure_logging
from gable.pipeline.live import build_runner
from gable.sheets import repository as repo
from gable.sheets.client import SheetClient

logger = logging.getLogger("gable.run_row")

GOOGLE_SCOPES: Final[tuple[str, ...]] = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
)


def read_one(client: SheetClient, tab: str, row_number: int) -> repo.Submission:
    """Read a single row and parse it the way the poller would.

    Args:
        client: A read-only sheet client.
        tab: The tab holding the row.
        row_number: The 1-based sheet row, as a person reads it in the browser.

    Returns:
        The submission, identical to what a poll would have produced.

    Raises:
        SheetError: when the tab has no recognisable header row.
        ValueError: when the row is past the end of the tab, or is blank.
    """
    rows = client.read(f"'{tab}'!A1:Z{row_number}")
    header_index, columns = repo.find_header(rows)
    if len(rows) < row_number:
        msg = f"{tab} has no row {row_number}"
        raise ValueError(msg)
    if row_number <= header_index + 1:
        msg = f"row {row_number} of {tab} is on or above its header row"
        raise ValueError(msg)
    row = rows[row_number - 1]
    if not any(cell.strip() for cell in row):
        msg = f"row {row_number} of {tab} is empty"
        raise ValueError(msg)
    return repo.submission_from_row(row, columns, row_number)


def _google_clients(settings: Settings) -> tuple[Any, Any, Any]:
    """Build the Sheets, Drive and Slides clients this run needs."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
    )
    return (
        build("sheets", "v4", credentials=credentials, cache_discovery=False),
        build("drive", "v3", credentials=credentials, cache_discovery=False),
        build("slides", "v1", credentials=credentials, cache_discovery=False),
    )


def main(argv: list[str] | None = None) -> int:
    """Start one row and report where it stopped.

    Args:
        argv: Command-line arguments, for testing. Defaults to `sys.argv`.

    Returns:
        A process exit code. Non-zero means nothing was started.

    Raises:
        Nothing. Every failure is logged and becomes an exit code.
    """
    parser = argparse.ArgumentParser(description="Run one sheet row through the live pipeline.")
    parser.add_argument("tab", help="the tab holding the row, e.g. Testing_1")
    parser.add_argument("row", type=int, help="the 1-based row number")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and print the row without starting a run or posting to Slack",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "continue this row's most recent run in its existing thread, reusing "
            "the photo already attached to it, instead of starting a new one"
        ),
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings.load(require_credentials=not args.dry_run)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        redact_secrets=settings.log_redact_secrets,
    )

    sheets, drive, slides = _google_clients(settings)
    client = SheetClient(spreadsheet_id=settings.sheet_id, service=sheets)
    try:
        submission = read_one(client, args.tab, args.row)
    except Exception:
        logger.exception("could not read %s row %d", args.tab, args.row)
        return 2

    intake = submission.intake
    logger.info(
        "read %s row %d: %s, %s, %s",
        args.tab,
        args.row,
        intake.agent_name or "no agent named",
        intake.request_type or "no request type",
        intake.address or "no address",
    )
    if args.dry_run:
        for field in (
            "agent_email",
            "agent_name",
            "request_type",
            "address",
            "post_details",
            "open_house",
            "new_price",
            "closing_price",
            "extra_notes",
            "side",
            "notes",
        ):
            print(f"{field:15} {getattr(intake, field)!r}")
        print(f"{'category':15} {intake.category!r}")
        print(f"{'price':15} {intake.price!r}")
        return 0

    connection = connect(settings.db_path)
    try:
        apply_migrations(connection)
        # The roster carries the phone the flyer needs, so it is refreshed here
        # for the same reason the poller refreshes it. It lives in the drive
        # now, beside the templates and the headshots.
        sync_contacts(drive, connection, settings.drive_id, settings.drive_templates_folder_id)
        store.record_submission(
            connection,
            submission.response_row_id,
            submission.sheet_row,
            submission.submitted_at,
            submission.intake,
            submission.content_hash,
        )

        from slack_sdk import WebClient

        slack = WebClient(token=settings.slack_bot_token)

        def say(text: str, thread_ts: str | None) -> str:
            """Post to Gable's own channel, in thread when there is one."""
            response = slack.chat_postMessage(
                channel=settings.slack_channel_id,
                text=text,
                thread_ts=thread_ts,
            )
            return str(response.get("ts") or thread_ts or "")

        if args.resume:
            existing = store.latest_run(connection, submission.response_row_id)
            if existing is None:
                logger.error("row %d has never been run, so there is nothing to resume", args.row)
                return 2
            if existing.is_terminal:
                logger.error("the most recent run for row %d is %s", args.row, existing.status)
                return 2
            # Reuse the photo already fitted and published for this run. A
            # resumed run must also stay in its own thread, or the answer
            # arrives in the channel detached from the question.
            runner = build_runner(
                settings,
                connection,
                drive,
                slides,
                say,
                hero_photo_url=existing.photo_url,
                origin_thread_ts=existing.slack_thread_ts,
            )
            result = runner.resume(submission, existing.run_id)
        else:
            runner = build_runner(settings, connection, drive, slides, say)
            result = runner.run(submission)
        logger.info("run %s finished as %s", result.run_id, result.status)
        for spoken in result.said:
            print(spoken)
        return 0
    except Exception:
        logger.exception("the run failed")
        return 2
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
