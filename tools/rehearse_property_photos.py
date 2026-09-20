"""Rehearse a multi-photo upload in the playground against a `Testing_1` row.

`TESTING.md` explains why a conversational test cannot be driven from Slack:
`routing.py` drops every event carrying a `bot_id`, so a scripted upload is
ignored whichever token sends it. This drives the two calls the listener makes
instead -- the real `PhotoHandoff` and the real runner -- against real Slack
files a person uploaded by hand, and prints the thread afterwards so it is read
the way Carmen reads it rather than out of the runs table.

Safety: the fixed playground channel, never the production one; an isolated
copy of the run database, so no production run is touched; and the production
spend ledger, so a rehearsal cannot spend past the ceiling that protects a real
listing. It never writes to the form-response tab.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from gable import spend
from gable.agents.contacts import sync_contacts
from gable.config import Settings
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.photos.batch import stored_photos
from gable.pipeline.live import build_runner
from gable.sheets.client import SheetClient
from gable.slackapp.client import build_web_client
from gable.slackapp.photos import PhotoHandoff
from gable.voice import is_clean
from tools.run_row import _google_clients, read_one

#: monarch-bot-playground. Never `C0BP597644B`, which is #calvo, where Carmen
#: and real staff are. Hardcoded rather than read from settings so no
#: environment can point a rehearsal at production.
PLAYGROUND: str = "C0B02721MNK"


class RehearsalError(RuntimeError):
    """Raised when a rehearsal cannot run, or did not end where it should."""


def _shared_ledger(budget: sqlite3.Connection) -> Callable[..., Any]:
    """Bind every paid call in this process to the production spend ledger.

    The isolated database is a copy, so its ledger is a copy too and spending
    against it would silently start the month again. Redirecting the module
    attribute is the only seam: `pipeline/live.py` reads `spend.guarded_call`
    off the module at call time.

    Args:
        budget: A connection to the production database.

    Returns:
        A replacement for `spend.guarded_call` that ignores the connection it
        is handed and reserves against the real ceiling instead.

    Raises:
        Nothing.
    """
    guarded = spend.guarded_call

    def shared(_connection: Any, *values: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Reserve against the production ledger, whatever database is in use."""
        return guarded(budget, *values, **kwargs)

    return shared


def main() -> None:
    """Build one playground flyer from photos already uploaded to Slack.

    Raises:
        RehearsalError: If the arguments are unusable, the isolated database
            would collide with production, or the run does not finish where a
            clean rehearsal ends.
        SystemExit: On an argument the parser rejects.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row", type=int, required=True, help="1-based Testing_1 row")
    parser.add_argument(
        "--file",
        action="append",
        required=True,
        help="Slack file id already uploaded to the playground; repeat for each photo",
    )
    parser.add_argument("--db", required=True, help="isolated copy of the run database")
    parser.add_argument(
        "--text",
        default="Make the first photo the main photo on the graphic.",
        help="the caption to rehearse; empty rehearses the ask-which-is-main path",
    )
    args = parser.parse_args()
    files: list[str] = list(args.file)
    if not 1 <= len(files) <= 3 or len(set(files)) != len(files):
        raise RehearsalError("provide between one and three distinct Slack file ids")

    load_dotenv("/opt/gable/.env", override=False)
    production = Settings.load()
    settings = replace(production, slack_channel_id=PLAYGROUND, db_path=Path(args.db))
    if settings.db_path == production.db_path:
        raise RehearsalError("--db must not be the production database")

    budget = connect(production.db_path)
    if not settings.db_path.exists():
        with sqlite3.connect(settings.db_path) as target:
            budget.backup(target)
    connection = connect(settings.db_path)
    apply_migrations(connection)
    spend.guarded_call = _shared_ledger(budget)

    sheets, drive, slides = _google_clients(settings)
    submission = read_one(
        SheetClient(spreadsheet_id=settings.sheet_id, service=sheets),
        "Testing_1",
        args.row,
        connection,
    )
    sync_contacts(drive, connection, settings.drive_id, settings.drive_templates_folder_id)
    store.record_submission(
        connection,
        submission.response_row_id,
        submission.sheet_row,
        submission.submitted_at,
        submission.intake,
        submission.content_hash,
        submission.source_tab,
    )
    slack = build_web_client(settings.slack_bot_token)

    def say(text: str, thread: str | None) -> str:
        """Post one house-style-checked line to the fixed playground channel."""
        if not is_clean(text):
            raise RehearsalError(f"rehearsal tried to say something off-style: {text!r}")
        answer = slack.chat_postMessage(channel=PLAYGROUND, text=text, thread_ts=thread)
        print(json.dumps({"said": text, "ts": answer["ts"]}), flush=True)
        return str(answer["ts"])

    first = build_runner(settings, connection, drive, slides, say).run(submission)
    run = store.run_by_id(connection, first.run_id)
    if run is None or not run.is_paused or not run.slack_thread_ts:
        raise RehearsalError(f"the row did not park waiting for a photo: {first.status}")
    thread = run.slack_thread_ts
    print(json.dumps({"run_id": run.run_id, "status": run.status, "thread": thread}), flush=True)

    def runner_for(db: sqlite3.Connection, url: str, thread_ts: str, progress: Any) -> Any:  # noqa: ANN401
        """Bind the production renderer to the isolated test run."""
        return build_runner(
            settings,
            db,
            drive,
            slides,
            say,
            hero_photo_url=url,
            origin_thread_ts=thread_ts,
            progress=progress,
        )

    handoff = PhotoHandoff(
        settings.db_path,
        settings.slack_bot_token,
        PLAYGROUND,
        settings.photo_max_edge_px,
        settings.photo_jpeg_quality,
        settings.photo_public_root,
        settings.photo_public_base,
        runner_for,
    )
    event: dict[str, Any] = {
        "channel": PLAYGROUND,
        "thread_ts": thread,
        "text": args.text,
        "files": [{"id": item} for item in files],
    }
    spoken = handoff.handle(event, slack, lambda stage: print(stage, flush=True))
    if spoken:
        say(spoken, thread)

    _report(connection, first.run_id, slack, thread)
    # The same upload announced twice must not build a second flyer. This is
    # the duplicate every listing is exposed to: Slack announces an upload as a
    # message AND as a file share.
    current = store.run_by_id(connection, first.run_id)
    before = current.output_file_id if current else ""
    repeated = handoff.handle(event, slack)
    after = store.run_by_id(connection, first.run_id)
    if repeated or after is None or after.output_file_id != before:
        raise RehearsalError(f"a repeated upload was not ignored: {repeated!r}")
    print(json.dumps({"duplicate_upload_ignored": True}), flush=True)


def _report(
    connection: sqlite3.Connection,
    run_id: str,
    slack: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    thread: str,
) -> None:
    """Print what the run ended as, and the thread exactly as a person reads it.

    Args:
        connection: The isolated database.
        run_id: The rehearsed run.
        slack: The authenticated Slack web client.
        thread: The listing thread root.

    Raises:
        Nothing. Judging the rehearsal is the reader's job, not this function's.
    """
    run = store.run_by_id(connection, run_id)
    print(
        json.dumps(
            {
                "status": run.status if run else "missing",
                "output": run.output_file_id if run else "",
                "url": run.output_url if run else "",
                "photos": [item["url"] for item in stored_photos(run.property_photos)]
                if run
                else [],
            }
        ),
        flush=True,
    )
    replies = slack.conversations_replies(channel=PLAYGROUND, ts=thread)["messages"]
    print(
        json.dumps({"thread": [str(message.get("text") or "") for message in replies]}, indent=1),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except RehearsalError as error:
        print(f"rehearsal stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from error
