"""Preview the exact form rows polling would consider new, without writing.

Run this on the deployed host after migrations and before enabling
``GABLE_POLL_ENABLED``. It opens SQLite in read-only mode, reads the configured
form tab, performs production identity reconciliation, and posts nothing.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from gable.config import ConfigError, Settings
from gable.db import store
from gable.db.schema import SCHEMA_VERSION
from gable.sheets import repository as repo
from gable.sheets.client import SheetClient


def pending_submissions(
    connection: sqlite3.Connection,
    submissions: list[repo.Submission],
) -> list[repo.Submission]:
    """Return one entry per reconciled id that has no run attempt."""
    pending: list[repo.Submission] = []
    seen: set[str] = set()
    for item in repo.reconcile_submissions(connection, submissions):
        if item.response_row_id in seen:
            continue
        seen.add(item.response_row_id)
        if not store.has_been_handled(connection, item.response_row_id):
            pending.append(item)
    return pending


def main(argv: list[str] | None = None) -> int:
    """Print the read-only poll candidate set and optionally require it empty."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-none",
        action="store_true",
        help="exit nonzero if any unhandled submission remains",
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(require_credentials=False)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        uri = f"file:{settings.db_path.resolve()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        version_row = connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        version = int(version_row["v"]) if version_row and version_row["v"] is not None else 0
        if version != SCHEMA_VERSION:
            print(
                f"Database schema is {version}; deploy and migrate schema {SCHEMA_VERSION} "
                "before previewing polling.",
                file=sys.stderr,
            )
            return 2
        client = SheetClient.build(settings.google_service_account_file, settings.sheet_id)
        submissions = repo.read_submissions(client, settings.tab_responses)
        pending = pending_submissions(connection, submissions)
        would_build = [item for item in pending if item.intake.wants_a_graphic]
        would_skip = [item for item in pending if not item.intake.wants_a_graphic]
        print(f"Rows read: {len(submissions)}")
        print(f"Unhandled rows polling would start: {len(would_build)}")
        for item in would_build:
            print(
                f"row {item.sheet_row}: {item.submitted_at} | {item.content_hash} | "
                f"{item.intake.request_type} | {item.intake.address}"
            )
        # Reported separately because these produce no design and no Slack
        # message. They are not nothing: each still writes one terminal `runs`
        # row, so `--expect-none` counts them and stays exactly as strict as it
        # was. Splitting them only stops a Reel reading as pending flyer work.
        print(f"Unhandled rows polling would skip as video or animation: {len(would_skip)}")
        for item in would_skip:
            print(
                f"row {item.sheet_row}: {item.submitted_at} | "
                f"{item.intake.content_type} | {item.intake.address}"
            )
        if args.expect_none and pending:
            return 1
        return 0
    except Exception as exc:
        print(f"Poll preview failed without writing: {exc}", file=sys.stderr)
        return 2
    finally:
        if "connection" in locals():
            connection.close()


if __name__ == "__main__":
    sys.exit(main())
