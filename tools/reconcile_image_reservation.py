"""Release one rejected image reservation without erasing its spend history.

This is an exceptional operator tool, not runtime retry logic. Use it only when
durable evidence proves that one exact request was rejected before the image
model executed. Preview is the default; ``--commit`` appends the reconciliation.

Example on the droplet:

    sudo -u gable /opt/gable/.venv/bin/python -m tools.reconcile_image_reservation \
        --db /opt/gable/var/gable.db --spend-id 46 \
        --reason invalid_request_dimensions \
        --evidence "HTTP 400; 1088x512 is below the documented 655360 pixels" \
        --commit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gable import spend
from gable.db.schema import apply_migrations, connect


def main(argv: list[str] | None = None) -> int:
    """Preview or append one evidenced reservation reconciliation.

    Args:
        argv: Command-line arguments. Defaults to ``sys.argv``.

    Returns:
        Zero on a successful preview or append; two on invalid input/state.

    Raises:
        Nothing. Operator-facing failures are translated into an exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="exact existing Gable database")
    parser.add_argument("--spend-id", required=True, type=int, help="exact spend ledger row")
    parser.add_argument("--reason", required=True, help="short rejection classification")
    parser.add_argument(
        "--evidence", required=True, help="specific evidence of pre-model rejection"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="append the release; without this flag the tool only previews",
    )
    args = parser.parse_args(argv)

    db_path = args.db.expanduser()
    if not db_path.is_file():
        print(f"Database does not exist: {db_path}", file=sys.stderr)
        return 2

    connection = connect(db_path)
    try:
        apply_migrations(connection)
        row = connection.execute(
            "SELECT id, run_id, service, model, units, unit_kind, note FROM spend WHERE id = ?",
            (args.spend_id,),
        ).fetchone()
        if row is None:
            print(f"Spend reservation {args.spend_id} does not exist.", file=sys.stderr)
            return 2
        print(f"database:       {db_path}")
        print(f"spend id:       {row['id']}")
        print(f"run id:         {row['run_id']}")
        print(f"service/model:  {row['service']} / {row['model']}")
        print(f"reservation:    {row['units']} {row['unit_kind']}")
        print(f"operation:      {row['note']}")
        print("spend total:    unchanged")

        if not args.commit:
            print("\nPreview only. Re-run with --commit to append the release.")
            return 0
        try:
            spend.release_rejected_image_reservation(
                connection,
                args.spend_id,
                reason=args.reason,
                evidence=args.evidence,
            )
        except (spend.OperationReleaseError, ValueError) as exc:
            print(f"Reservation was not released: {exc}", file=sys.stderr)
            return 2
        print("\nRelease appended. The original reservation still counts toward the $50 ceiling.")
        return 0
    except Exception as exc:
        print(f"Reservation reconciliation failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
