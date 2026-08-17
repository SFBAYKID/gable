"""Report what using Gable actually felt like, one listing per line.

Every defect found on 2026-08-17 was found the same way: Chase read a Slack
thread and saw something wrong. Nothing measured the experience itself, so a
listing that took six replies looked identical in the database to one that took
none — both say `delivered`.

This counts the thing that decides whether Carmen keeps using it. A listing that
reached a flyer after one answer worked. A listing that took four is the
"I'll just do it myself" case, whatever its final status says.

It reads the run history only: no Slack calls, no Google calls, no writes. Safe
to run against production at any time, and safe to run on a schedule.

Does not handle: judging whether a flyer looks right. That is the vision gate's
job, and this reports what the gate concluded rather than re-deciding it.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gable.config import ConfigError, Settings
from gable.db.run_store import PAUSED
from gable.db.schema import connect

#: More asks than this and the listing has stopped being worth the automation.
#: One is the design: the batched ask exists so a person answers once.
ROUND_TRIP_BUDGET: Final[int] = 1


@dataclass(frozen=True, slots=True)
class Listing:
    """One submission's whole history, measured from its run events."""

    address: str
    request_type: str
    runs: int
    asks: int
    status: str
    photo_noted: bool
    delivered: bool

    @property
    def over_budget(self) -> bool:
        """Whether it asked more times than the batched ask is meant to need."""
        return self.asks > ROUND_TRIP_BUDGET

    @property
    def verdict(self) -> str:
        """A short judgement written for a person reading a list of these."""
        if not self.delivered:
            return f"NOT DELIVERED ({self.status})"
        if self.over_budget:
            return f"{self.asks} asks — too many"
        if self.photo_noted:
            return "delivered with a photo note"
        return "clean"


def _listings(connection: sqlite3.Connection, since: str) -> list[Listing]:
    """Assemble one record per submission that has any run since `since`.

    Args:
        connection: An open Gable database connection.
        since: ISO timestamp lower bound on run creation.

    Returns:
        One `Listing` per submission, newest first.

    Raises:
        sqlite3.Error: on a query failure.
    """
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT s.address, s.request_type, s.response_row_id, MAX(r.created_at) AS last_at
        FROM runs r JOIN submissions s ON s.response_row_id = r.response_row_id
        WHERE r.created_at >= ?
        GROUP BY s.response_row_id
        ORDER BY last_at DESC
        """,
        (since,),
    ).fetchall()

    out: list[Listing] = []
    for row in rows:
        runs = connection.execute(
            "SELECT run_id, status FROM runs WHERE response_row_id = ? ORDER BY created_at",
            (row["response_row_id"],),
        ).fetchall()
        ids = [str(r["run_id"]) for r in runs]
        marks = ",".join("?" for _ in ids)
        # Each transition INTO a paused state is one thing Gable asked a person
        # to do. Counting events rather than current status is the point: the
        # status only ever shows the last one.
        asks = connection.execute(
            f"SELECT COUNT(*) FROM run_events WHERE run_id IN ({marks}) "
            f"AND status IN ({','.join('?' for _ in PAUSED)})",
            (*ids, *sorted(PAUSED)),
        ).fetchone()[0]
        noted = connection.execute(
            f"SELECT COUNT(*) FROM run_events WHERE run_id IN ({marks}) "
            "AND detail LIKE '%noticed%'",
            ids,
        ).fetchone()[0]
        out.append(
            Listing(
                address=str(row["address"]),
                request_type=str(row["request_type"]),
                runs=len(ids),
                asks=int(asks),
                status=str(runs[-1]["status"]),
                photo_noted=bool(noted),
                delivered=any(str(r["status"]) == "delivered" for r in runs),
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    """Print one line per listing, worst first, and a summary.

    Args:
        argv: Command-line arguments, for testing. Defaults to `sys.argv`.

    Returns:
        0 when every delivered listing stayed inside the round-trip budget,
        1 when any did not. Non-zero is deliberate: this is meant to be usable
        as a scheduled check that fails loudly rather than a report nobody reads.

    Raises:
        Nothing. A configuration or database failure becomes an exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="2026-08-01",
        help="ISO date lower bound on run creation (default 2026-08-01)",
    )
    parser.add_argument("--db", default="", help="database path, defaulting to the configured one")
    args = parser.parse_args(argv)

    try:
        db_path = Path(args.db) if args.db else Settings.load(require_credentials=False).db_path
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not db_path.is_file():
        print(f"no database at {db_path}", file=sys.stderr)
        return 2

    connection = connect(db_path)
    try:
        listings = _listings(connection, args.since)
    except sqlite3.Error as exc:
        print(f"could not read the run history: {exc}", file=sys.stderr)
        return 2
    finally:
        connection.close()

    if not listings:
        print(f"no runs since {args.since}")
        return 0

    listings.sort(key=lambda item: (item.delivered, -item.asks))
    print(f"{'asks':>4}  {'runs':>4}  {'request':<22}  {'address':<44}  verdict")
    for item in listings:
        print(
            f"{item.asks:>4}  {item.runs:>4}  {item.request_type[:22]:<22}  "
            f"{item.address[:44]:<44}  {item.verdict}"
        )

    delivered = [item for item in listings if item.delivered]
    over = [item for item in delivered if item.over_budget]
    stuck = [item for item in listings if not item.delivered]
    print()
    print(f"{len(listings)} listing(s) since {args.since}")
    print(f"  delivered: {len(delivered)}")
    print(f"  never delivered: {len(stuck)}")
    print(f"  delivered over the {ROUND_TRIP_BUDGET}-ask budget: {len(over)}")
    if delivered:
        worst = max(delivered, key=lambda item: item.asks)
        average = sum(item.asks for item in delivered) / len(delivered)
        print(f"  asks per delivered listing: {average:.1f} average, {worst.asks} worst")
        print(f"  worst was {worst.address[:60]}")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
