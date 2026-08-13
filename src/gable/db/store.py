"""Reading and writing Gable's own record of what it has done.

The one question this exists to answer cheaply: **have I already built this?**
Without it the poller re-renders every historical row on first run, and the live
sheet has 99 of them.

Everything is small, explicit SQL. `sqlite3` is in the standard library and the
queries fit on a screen, so an ORM would buy indirection rather than clarity.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from gable.db.run_store import (
    ACTIVE,
    MAX_RUN_ATTEMPTS,
    PAUSED,
    TERMINAL,
    RunAlreadyActiveError,
    RunCounts,
    RunLimitReachedError,
    RunRow,
    claim_paused_run,
    latest_run,
    paused_runs,
    recover_interrupted_runs,
    run_attempt_count,
    run_by_id,
    run_for_thread,
    set_status,
    start_run,
    status_counts,
)
from gable.db.template_store import (
    TemplateAudit,
    adopt_template_catalog,
    record_template_audit,
    template_audit,
    template_catalog_adopted,
    template_for_thread,
)
from gable.listings.intake import Intake

__all__ = [
    "ACTIVE",
    "MAX_RUN_ATTEMPTS",
    "PAUSED",
    "TERMINAL",
    "RunAlreadyActiveError",
    "RunCounts",
    "RunLimitReachedError",
    "RunRow",
    "TemplateAudit",
    "adopt_template_catalog",
    "claim_paused_run",
    "latest_run",
    "paused_runs",
    "record_template_audit",
    "recover_interrupted_runs",
    "run_attempt_count",
    "run_by_id",
    "run_for_thread",
    "set_status",
    "start_run",
    "status_counts",
    "template_audit",
    "template_catalog_adopted",
    "template_for_thread",
]


def _now() -> str:
    """An ISO 8601 UTC timestamp, the only time format this database stores."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class StoredSubmission:
    """One intake row reconstructed from Gable's local database."""

    response_row_id: str
    sheet_row: int
    submitted_at: str
    intake: Intake
    content_hash: str


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
        response_row_id: The submission's deployed tuple-hash identity.
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
        "SELECT content_hash FROM submissions WHERE response_row_id = ?",
        (response_row_id,),
    ).fetchone()
    if existing:
        # Any answer can be corrected in place while the form timestamp keeps
        # the same stable identity. Keep the stored payload current so a paused
        # resume or explicit retry does not rebuild from stale form data.
        # Ordinary polling remains suppressed by the existing run state; this
        # update alone never opens an attempt.
        if content_hash and str(existing["content_hash"] or "") != content_hash:
            connection.execute(
                """
                UPDATE submissions
                   SET sheet_row = ?, submitted_at = ?, agent_email = ?, agent_name = ?,
                       request_type = ?, address = ?, post_details = ?, open_house = ?,
                       new_price = ?, closing_price = ?, extra_notes = ?, side = ?,
                       notes = ?, content_hash = ?
                 WHERE response_row_id = ?
                """,
                (
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
                    content_hash,
                    response_row_id,
                ),
            )
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


def load_submission(
    connection: sqlite3.Connection, response_row_id: str
) -> StoredSubmission | None:
    """Reconstruct a submission so a paused Slack thread can resume it.

    Args:
        connection: An open connection.
        response_row_id: The submission's identity.

    Returns:
        The stored submission, or None when the id is unknown.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        """
        SELECT response_row_id, sheet_row, submitted_at, agent_email, agent_name,
               request_type, address, post_details, open_house, new_price,
               closing_price, extra_notes, side, notes, content_hash
          FROM submissions
         WHERE response_row_id = ?
        """,
        (response_row_id,),
    ).fetchone()
    if not row:
        return None
    return StoredSubmission(
        response_row_id=row["response_row_id"],
        sheet_row=int(row["sheet_row"]),
        submitted_at=row["submitted_at"],
        intake=Intake(
            agent_email=row["agent_email"],
            agent_name=row["agent_name"],
            request_type=row["request_type"],
            address=row["address"],
            post_details=row["post_details"],
            open_house=row["open_house"],
            new_price=row["new_price"],
            closing_price=row["closing_price"],
            extra_notes=row["extra_notes"],
            side=row["side"],
            notes=row["notes"],
        ),
        content_hash=row["content_hash"],
    )


def response_id_for_timestamp(connection: sqlite3.Connection, submitted_at: str) -> str | None:
    """Resolve one immutable form timestamp to its deployed submission id.

    Args:
        connection: An open connection.
        submitted_at: Google Forms timestamp exactly as stored for the row.

    Returns:
        The existing response id, or ``None`` when this timestamp is new.

    Raises:
        ValueError: If historical data contains more than one submission with
            the same timestamp. Choosing either would merge two listings.
        sqlite3.Error: On a read failure.
    """
    rows = connection.execute(
        "SELECT response_row_id FROM submissions WHERE submitted_at = ?",
        (submitted_at,),
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("more than one stored submission has the same form timestamp")
    return str(rows[0]["response_row_id"]) if rows else None


def has_been_handled(connection: sqlite3.Connection, response_row_id: str) -> bool:
    """Whether polling this submission again would be unsafe or redundant.

    This is the idempotency test the poller runs on every row.

    Args:
        connection: An open connection.
        response_row_id: The submission's identity.

    Returns:
        True after any attempt exists. Scheduled polling starts only genuinely
        unseen rows; human-paused work resumes the same run, and fresh retries
        are explicit operator actions.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT 1 FROM runs WHERE response_row_id = ? LIMIT 1",
        (response_row_id,),
    ).fetchone()
    return row is not None


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


def upsert_salesperson(
    connection: sqlite3.Connection,
    *,
    email: str,
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    headshot_url: str = "",
    brokerage_url: str = "",
) -> None:
    """Store or refresh one agent from the roster.

    Args:
        connection: An open connection.
        email: The agent's address, which is the key.
        first_name: Given name, as the roster writes it.
        last_name: Family name.
        phone: Their direct line.
        headshot_url: Only when the roster carries one. The face normally comes
            from the Head Shots folder at render time instead.
        brokerage_url: Their page on the brokerage site, when known.

    Raises:
        sqlite3.Error: on a write failure.
    """
    connection.execute(
        """
        INSERT INTO salespeople (email, first_name, last_name, phone, template,
                                 headshot_url, brokerage_url, synced_at)
        VALUES (?,?,?,?,'',?,?,?)
        ON CONFLICT(email) DO UPDATE SET
            first_name=excluded.first_name, last_name=excluded.last_name,
            phone=excluded.phone, headshot_url=excluded.headshot_url,
            brokerage_url=excluded.brokerage_url, synced_at=excluded.synced_at
        """,
        (
            email.strip().lower(),
            first_name.strip(),
            last_name.strip(),
            phone.strip(),
            headshot_url.strip(),
            brokerage_url.strip(),
            _now(),
        ),
    )


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
