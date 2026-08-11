"""Run one guarded pipeline pass locally, with Slack entirely absent.

Production joins Socket Mode and the long-running poller in
``gable.slackapp.runtime``. This entry point deliberately does less: it checks
the backfill guard, reads the Sheet once, and gives new submissions to the real
runner while printing its messages locally. That keeps the pipeline runnable on
a development machine with no Slack package configuration or live listener.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Final

from gable.config import ConfigError, Settings
from gable.db.schema import apply_migrations, connect
from gable.logging_setup import configure_logging
from gable.pipeline.live import build_runner
from gable.pipeline.poller import Poller
from gable.sheets import repository as repo
from gable.sheets.client import SheetClient

logger = logging.getLogger("gable.cli")

GOOGLE_SCOPES: Final[tuple[str, ...]] = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
)


def _local_requirements(settings: Settings) -> list[str]:
    """Name missing non-Slack configuration needed for a real local pass."""
    missing: list[str] = []
    if not settings.google_service_account_file.is_file():
        missing.append("GOOGLE_SERVICE_ACCOUNT_FILE must name an existing key file")
    if not settings.drive_id:
        missing.append("GABLE_DRIVE_ID is required")
    if not settings.drive_output_folder_id:
        missing.append("GABLE_DRIVE_OUTPUT_FOLDER_ID is required")
    return missing


def _build_google_clients(settings: Settings) -> tuple[Any, Any, Any]:
    """Construct Sheets, Drive, and Slides clients without importing Slack."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
    )
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    slides = build("slides", "v1", credentials=credentials, cache_discovery=False)
    return sheets, drive, slides


def main() -> int:
    """Run one safe poll cycle locally and return a process exit code."""
    try:
        settings = Settings.load(require_credentials=False)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        redact_secrets=settings.log_redact_secrets,
    )
    missing = _local_requirements(settings)
    if missing:
        for problem in missing:
            logger.error("%s", problem)
        return 2

    connection = connect(settings.db_path)
    try:
        apply_migrations(connection)
        sheets, drive, slides = _build_google_clients(settings)
        sheet_client = SheetClient(spreadsheet_id=settings.sheet_id, service=sheets)

        def local_post(text: str, thread_ts: str | None) -> str:
            """Print runner messages and preserve its thread-shaped seam."""
            print(text)
            return thread_ts or "local"

        def on_submission(submission: repo.Submission) -> None:
            """Run one submission using the real clients and local messages."""
            runner = build_runner(settings, connection, drive, slides, local_post)
            runner.run(submission)

        poller = Poller(
            client=sheet_client,
            connection=connection,
            responses_tab=settings.tab_responses,
            salespeople_tab=settings.tab_agents,
            on_submission=on_submission,
            schedule=settings.poll_schedule,
            max_per_pass=settings.max_batch,
        )
        ready, reason = poller.ready()
        if not ready:
            logger.error("%s", reason)
            return 2
        started = poller.one_pass()
        logger.info("local pass started %d submission(s)", started)
        return 0
    except Exception:
        logger.exception("the local pipeline pass failed")
        return 2
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
