"""Reading and writing Gable's own record of what it has done.

The one question this exists to answer cheaply: **have I already built this?**
Without it the poller re-renders every historical row on first run, and the live
sheet has 99 of them.

Everything is small, explicit SQL. `sqlite3` is in the standard library and the
queries fit on a screen, so an ORM would buy indirection rather than clarity.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from gable.listings.intake import Intake

#: Statuses that mean a submission is finished and must never be rebuilt.
TERMINAL: Final[frozenset[str]] = frozenset({"delivered", "failed", "skipped"})

#: Statuses that mean a run is waiting on a human. Not terminal — these resume.
PAUSED: Final[frozenset[str]] = frozenset({"needs_photo", "needs_info", "needs_template"})


def _now() -> str:
    """An ISO 8601 UTC timestamp, the only time format this database stores."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class RunRow:
    """One attempt at turning a submission into a post."""

    run_id: str
    response_row_id: str
    status: str
    output_url: str = ""
    slack_thread_ts: str = ""
    failure_reason: str = ""

    @property
    def is_terminal(self) -> bool:
        """True when this run is finished and must not be reprocessed."""
        return self.status in TERMINAL

    @property
    def is_paused(self) -> bool:
        """True when this run is waiting on a human rather than on Gable."""
        return self.status in PAUSED


def record_submission(
    connection: sqlite3.Connection,
    response_row_id: str,
    sheet_row: int,
    submitted_at: str,
    intake: Intake,
    content_hash: str = "",
) -> bool:
    """Store a submission if it is new.

    Args:
        connection: An open connection.
        response_row_id: The content-derived identity from `models`.
        sheet_row: 1-based row number, for pointing a human at it.
        submitted_at: The form timestamp, as text.
        intake: The parsed columns.
        content_hash: Hash of the whole row, so a later edit is detectable even
            though the identity is stable.

    Returns:
        True if this was the first time seeing it, False if already known.

    Raises:
        sqlite3.Error: on a write failure.
    """
    existing = connection.execute(
        "SELECT 1 FROM submissions WHERE response_row_id = ?", (response_row_id,)
    ).fetchone()
    if existing:
        return False
    connection.execute(
        """
        INSERT INTO submissions (
            response_row_id, sheet_row, submitted_at, agent_email, agent_name,
            request_type, address, post_details, open_house, new_price,
            closing_price, extra_notes, side, notes, first_seen_at, content_hash
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            response_row_id,
            sheet_row,
            submitted_at,
            intake.agent_email,
            intake.agent_name,
            intake.request_type,
            intake.address,
            intake.post_details,
            intake.open_house,
            intake.new_price,
            intake.closing_price,
            intake.extra_notes,
            intake.side,
            intake.notes,
            _now(),
            content_hash,
        ),
    )
    return True


def has_been_handled(connection: sqlite3.Connection, response_row_id: str) -> bool:
    """Whether this submission already reached a terminal state.

    This is the idempotency test the poller runs on every row.

    Args:
        connection: An open connection.
        response_row_id: The submission's identity.

    Returns:
        True when a run for it finished, so it must not be rebuilt.

    Raises:
        sqlite3.Error: on a query failure.
    """
    placeholders = ",".join("?" * len(TERMINAL))
    row = connection.execute(
        f"SELECT 1 FROM runs WHERE response_row_id = ? AND status IN ({placeholders}) LIMIT 1",
        (response_row_id, *sorted(TERMINAL)),
    ).fetchone()
    return row is not None


def start_run(connection: sqlite3.Connection, response_row_id: str) -> RunRow:
    """Open a new run for a submission.

    Args:
        connection: An open connection.
        response_row_id: The submission this run is for.

    Returns:
        The new run, status `pending`.

    Raises:
        sqlite3.Error: on a write failure, including a foreign-key violation if
            the submission was never recorded.
    """
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = _now()
    connection.execute(
        "INSERT INTO runs (run_id, response_row_id, status, created_at, updated_at)"
        " VALUES (?,?,?,?,?)",
        (run_id, response_row_id, "pending", now, now),
    )
    connection.execute(
        "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
        (run_id, now, "pending", "run opened"),
    )
    return RunRow(run_id=run_id, response_row_id=response_row_id, status="pending")


def set_status(
    connection: sqlite3.Connection,
    run_id: str,
    status: str,
    detail: str = "",
    **fields: str | int,
) -> None:
    """Move a run to a new status and append the event.

    Args:
        connection: An open connection.
        run_id: Which run.
        status: The new status.
        detail: A human-readable note for the event log.
        **fields: Any run columns to update alongside, e.g. `output_url`.

    Raises:
        ValueError: if a field name is not a real column. Building SQL from
            caller-supplied names is only safe with a whitelist.
        sqlite3.Error: on a write failure.
    """
    allowed = {
        "template_file_id",
        "template_label",
        "output_file_id",
        "output_url",
        "photo_url",
        "photo_source",
        "ai_generated",
        "slack_thread_ts",
        "failure_reason",
    }
    unknown = set(fields) - allowed
    if unknown:
        msg = f"not run columns: {sorted(unknown)}"
        raise ValueError(msg)

    now = _now()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    sql = "UPDATE runs SET status = ?, updated_at = ?"
    if assignments:
        sql += f", {assignments}"
    sql += " WHERE run_id = ?"
    connection.execute(sql, (status, now, *fields.values(), run_id))
    connection.execute(
        "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
        (run_id, now, status, detail),
    )


def latest_run(connection: sqlite3.Connection, response_row_id: str) -> RunRow | None:
    """The most recent run for a submission.

    Args:
        connection: An open connection.
        response_row_id: The submission's identity.

    Returns:
        The newest run, or None if there has never been one.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT run_id, response_row_id, status, output_url, slack_thread_ts, failure_reason"
        " FROM runs WHERE response_row_id = ? ORDER BY created_at DESC LIMIT 1",
        (response_row_id,),
    ).fetchone()
    return _to_run(row) if row else None


def run_for_thread(connection: sqlite3.Connection, thread_ts: str) -> RunRow | None:
    """Find the run a Slack thread belongs to.

    This is what lets Carmen reply "use the other photo" in a thread and have
    Gable know which listing she means.

    Args:
        connection: An open connection.
        thread_ts: The Slack thread timestamp.

    Returns:
        The matching run, or None.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT run_id, response_row_id, status, output_url, slack_thread_ts, failure_reason"
        " FROM runs WHERE slack_thread_ts = ? ORDER BY created_at DESC LIMIT 1",
        (thread_ts,),
    ).fetchone()
    return _to_run(row) if row else None


def paused_runs(connection: sqlite3.Connection) -> list[RunRow]:
    """Every run waiting on a human.

    Args:
        connection: An open connection.

    Returns:
        Runs in `needs_photo`, `needs_info` or `needs_template`, oldest first,
        so `/gable run` resumes them in the order they were asked about.

    Raises:
        sqlite3.Error: on a query failure.
    """
    placeholders = ",".join("?" * len(PAUSED))
    rows = connection.execute(
        "SELECT run_id, response_row_id, status, output_url, slack_thread_ts, failure_reason"
        f" FROM runs WHERE status IN ({placeholders}) ORDER BY created_at",
        tuple(sorted(PAUSED)),
    ).fetchall()
    return [_to_run(row) for row in rows]


def _to_run(row: sqlite3.Row) -> RunRow:
    """Turn a result row into a `RunRow`."""
    return RunRow(
        run_id=row["run_id"],
        response_row_id=row["response_row_id"],
        status=row["status"],
        output_url=row["output_url"] or "",
        slack_thread_ts=row["slack_thread_ts"] or "",
        failure_reason=row["failure_reason"] or "",
    )


def address_key(address: str) -> str:
    """A stable key for caching facts about a property.

    Args:
        address: As typed, with whatever spacing and case.

    Returns:
        A lowercase key with punctuation and repeated spaces removed, so
        "7940 Oakwood Rd, Glen Burnie, MD 21061" and "7940 oakwood rd  glen
        burnie md 21061" hit the same cache entry.

    Raises:
        Nothing.
    """
    stripped = "".join(c for c in address.lower() if c.isalnum() or c.isspace())
    return " ".join(stripped.split())


def remember_facts(
    connection: sqlite3.Connection,
    address: str,
    facts: dict[str, str],
    source_url: str = "",
    confidence: float = 0.0,
) -> None:
    """Cache what was looked up about a property.

    Args:
        connection: An open connection.
        address: The property address.
        facts: Any of `beds`, `baths`, `square_feet`, `list_price`, `year_built`.
        source_url: Where it came from, for the audit trail.
        confidence: How sure the lookup was, 0.0 to 1.0.

    Raises:
        sqlite3.Error: on a write failure.
    """
    connection.execute(
        """
        INSERT INTO property_facts (address_key, address, beds, baths, square_feet,
                                    list_price, year_built, source_url, confidence, looked_up_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(address_key) DO UPDATE SET
            beds=excluded.beds, baths=excluded.baths, square_feet=excluded.square_feet,
            list_price=excluded.list_price, year_built=excluded.year_built,
            source_url=excluded.source_url, confidence=excluded.confidence,
            looked_up_at=excluded.looked_up_at
        """,
        (
            address_key(address),
            address,
            facts.get("beds", ""),
            facts.get("baths", ""),
            facts.get("square_feet", ""),
            facts.get("list_price", ""),
            facts.get("year_built", ""),
            source_url,
            confidence,
            _now(),
        ),
    )


def recall_facts(connection: sqlite3.Connection, address: str) -> dict[str, str]:
    """What is already known about a property.

    Args:
        connection: An open connection.
        address: The property address.

    Returns:
        The cached facts, empty if it has never been looked up. Only non-empty
        values are returned, so a caller can tell "not known" from "known to be
        blank".

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT beds, baths, square_feet, list_price, year_built, source_url, confidence"
        " FROM property_facts WHERE address_key = ?",
        (address_key(address),),
    ).fetchone()
    if not row:
        return {}
    columns = ("beds", "baths", "square_feet", "list_price", "year_built", "source_url")
    return {name: row[name] for name in columns if row[name]}


def record_spend(
    connection: sqlite3.Connection,
    service: str,
    run_id: str = "",
    model: str = "",
    units: float = 0.0,
    unit_kind: str = "",
    note: str = "",
) -> None:
    """Log a paid call so a bill can be reconstructed.

    Written even when the call failed: tokens spent before an error still cost
    money, and a spend log that only records successes understates the bill.

    Args:
        connection: An open connection.
        service: `openai`, `firecrawl`, `google`.
        run_id: The run it belongs to, if any.
        model: The model used.
        units: How many tokens, images or searches.
        unit_kind: What `units` counts.
        note: Anything worth knowing later.

    Raises:
        sqlite3.Error: on a write failure.
    """
    connection.execute(
        "INSERT INTO spend (run_id, at, service, model, units, unit_kind, note)"
        " VALUES (?,?,?,?,?,?,?)",
        (run_id, _now(), service, model, units, unit_kind, note),
    )
