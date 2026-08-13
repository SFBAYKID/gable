"""Mark everything currently on the Sheet as history, without building any of it.

Run once, on a fresh deployment, before the poller starts. Until this has run,
`Poller.ready()` refuses — and that refusal is the guard between a first boot and
99 flyers arriving in Slack at once.

    python tools/adopt_backfill.py            # show what would be adopted
    python tools/adopt_backfill.py --commit   # actually adopt it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gable.config import ConfigError, Settings
from gable.db.schema import apply_migrations, connect
from gable.sheets import repository as repo
from gable.sheets.client import SheetClient


def main() -> int:
    """Adopt the historical rows.

    Returns:
        0 on success, 1 if the sheet could not be read.

    Raises:
        Nothing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write, rather than preview")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="database path; defaults to GABLE_DB_PATH",
    )
    args = parser.parse_args()

    try:
        settings = Settings.load(require_credentials=False)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    db_path = args.db or settings.db_path
    connection = connect(db_path)
    apply_migrations(connection)

    try:
        try:
            client = SheetClient.build(str(settings.google_service_account_file), settings.sheet_id)
            submissions = repo.read_submissions(client, settings.tab_responses)
        except Exception as exc:
            print(f"Could not read the sheet: {type(exc).__name__}")
            return 1

        already = repo.backfill_adopted(connection)
        print(f"database:            {db_path}")
        print(f"rows on the sheet:   {len(submissions)}")
        print(f"already adopted:     {already}")

        if already:
            print("\nNothing to do — the backfill has already been adopted.")
            return 0
        if not args.commit:
            print(f"\nWould adopt {len(submissions)} rows as history and build none of them.")
            print("Re-run with --commit to do it.")
            return 0

        adopted = repo.adopt_backfill(connection, submissions)
        print(f"\nAdopted {adopted} rows as history. Nothing was built.")
        print("The poller will now only act on submissions that arrive from here on.")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
