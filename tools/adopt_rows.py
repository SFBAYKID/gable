"""Preview or adopt exact historical form rows after identity migration.

This is deliberately narrower than the first-install backfill. Each requested
row must include the full-row content hash printed by preview, so a moved or
edited Sheet cannot cause the wrong submission to be skipped.

    python -m tools.adopt_rows "Form Responses 1" 46:1d63ec043ba9ccdf
    python -m tools.adopt_rows "Form Responses 1" 46:1d63ec043ba9ccdf --commit
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from sqlite3 import Connection

from gable.config import ConfigError, Settings
from gable.db import store
from gable.db.schema import SCHEMA_VERSION, apply_migrations, connect
from gable.sheets.client import SheetClient
from gable.sheets.identity import Submission, remember_source_rows
from gable.sheets.repository import backfill_adopted, read_submissions, reconcile_submissions


@dataclass(frozen=True, slots=True)
class Assertion:
    """One physical row and the exact content expected there."""

    sheet_row: int
    content_hash: str


def parse_assertion(raw: str) -> Assertion:
    """Parse ``ROW:HASH`` while refusing weak or malformed assertions."""
    row_text, separator, content_hash = raw.partition(":")
    if not separator or not row_text.isdigit() or int(row_text) < 2:
        raise argparse.ArgumentTypeError("expected ROW:16_CHARACTER_CONTENT_HASH")
    clean_hash = content_hash.strip().lower()
    if len(clean_hash) != 16 or any(
        character not in "0123456789abcdef" for character in clean_hash
    ):
        raise argparse.ArgumentTypeError("content hash must be exactly 16 hexadecimal characters")
    return Assertion(sheet_row=int(row_text), content_hash=clean_hash)


def select_asserted(
    submissions: list[Submission],
    assertions: list[Assertion],
) -> list[Submission]:
    """Return exact targets or refuse the complete batch before any write."""
    if len({item.sheet_row for item in assertions}) != len(assertions):
        raise ValueError("each sheet row may be asserted only once")
    by_row = {item.sheet_row: item for item in submissions}
    selected: list[Submission] = []
    for assertion in assertions:
        item = by_row.get(assertion.sheet_row)
        if item is None:
            raise ValueError(f"sheet row {assertion.sheet_row} is not a response")
        if item.content_hash != assertion.content_hash:
            raise ValueError(
                f"sheet row {assertion.sheet_row} changed; expected content hash "
                f"{assertion.content_hash}, found {item.content_hash}"
            )
        selected.append(item)
    return selected


def adopt_asserted(
    connection: Connection,
    submissions: list[Submission],
    assertions: list[Assertion],
    source_tab: str,
) -> int:
    """Atomically mark only exact, currently unhandled rows as historical."""
    if not backfill_adopted(connection):
        raise ValueError("the initial historical backfill has not been adopted")
    reconciled = reconcile_submissions(connection, submissions)
    selected = select_asserted(reconciled, assertions)
    adopted = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        # The alias ledger references submissions and represents the complete
        # snapshot. Insert identities the database has never seen so that
        # foreign keys are satisfied, but never rewrite an existing payload as
        # a side effect of adopting some other row. Ordinary polling and an
        # owned-thread source refresh remain the intentional paths for applying
        # a form correction to an existing run.
        for item in reconciled:
            if store.load_submission(connection, item.response_row_id) is not None:
                continue
            store.record_submission(
                connection,
                item.response_row_id,
                item.sheet_row,
                item.submitted_at,
                item.intake,
                item.content_hash,
                item.source_tab,
            )
        for item in selected:
            if store.has_been_handled(connection, item.response_row_id):
                continue
            run = store.start_run(connection, item.response_row_id)
            store.set_status(
                connection,
                run.run_id,
                "skipped",
                "explicitly asserted as historical; no flyer or Slack message was created",
            )
            adopted += 1
        remember_source_rows(connection, reconciled, frozenset({source_tab}))
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return adopted


def main(argv: list[str] | None = None) -> int:
    """Preview exact rows by default; write only with an explicit commit flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tab", help="exact form-response tab")
    parser.add_argument("assertions", nargs="+", type=parse_assertion, help="ROW:CONTENT_HASH")
    parser.add_argument("--commit", action="store_true", help="record these exact rows as skipped")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(require_credentials=False)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.commit:
        connection = connect(settings.db_path)
        apply_migrations(connection)
    else:
        connection = sqlite3.connect(
            f"file:{settings.db_path.resolve()}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        version = int(row["v"]) if row and row["v"] is not None else 0
        if version != SCHEMA_VERSION:
            print(
                f"Database schema is {version}; deploy and migrate schema {SCHEMA_VERSION} "
                "before previewing adoption.",
                file=sys.stderr,
            )
            connection.close()
            return 2
    try:
        client = SheetClient.build(settings.google_service_account_file, settings.sheet_id)
        submissions = read_submissions(client, args.tab)
        reconciled = reconcile_submissions(connection, submissions)
        selected = select_asserted(reconciled, args.assertions)
        for item in selected:
            handled = store.has_been_handled(connection, item.response_row_id)
            print(
                f"row {item.sheet_row}: {item.submitted_at} | {item.content_hash} | "
                f"{item.intake.request_type} | {item.intake.address} | "
                f"{'already handled' if handled else 'would adopt as historical'}"
            )
        if not args.commit:
            print("Preview only. Nothing was written; re-run with --commit after review.")
            return 0
        adopted = adopt_asserted(connection, submissions, args.assertions, args.tab)
        print(f"Adopted {adopted} exact historical row(s). No Slack message or flyer was created.")
        return 0
    except Exception as exc:
        print(f"Nothing was written: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
