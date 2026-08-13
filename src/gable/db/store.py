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
TERMINAL: Final[frozenset[str]] = frozenset(("delivered", "failed", "skipped"))

#: Statuses that mean a run is waiting on a human. Not terminal — these resume.
PAUSED: Final[frozenset[str]] = frozenset(
    {"needs_photo", "needs_info", "needs_template", "needs_review"}
)

#: An unattended failure may be retried, but never indefinitely. This is a hard
#: ceiling in the run-opening path, not a convention callers have to remember.
MAX_RUN_ATTEMPTS: Final[int] = 3


class RunLimitReachedError(RuntimeError):
    """Raised when opening another run would exceed the hard attempt ceiling."""


def _now() -> str:
    """An ISO 8601 UTC timestamp, the only time format this database stores."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class RunRow:
    """One attempt at turning a submission into a post."""

    run_id: str
    response_row_id: str
    status: str
    output_file_id: str = ""
    output_url: str = ""
    slack_thread_ts: str = ""
    failure_reason: str = ""
    template_file_id: str = ""
    template_label: str = ""
    #: The fitted, published hero photo, once one has been attached. Carried
    #: here so a paused run can be continued without asking for it again.
    photo_url: str = ""
    photo_source: str = ""
    ai_enhanced: bool = False

    @property
    def is_terminal(self) -> bool:
        """True when this run is finished and must not be reprocessed."""
        return self.status in TERMINAL

    @property
    def is_paused(self) -> bool:
        """True when this run is waiting on a human rather than on Gable."""
        return self.status in PAUSED


@dataclass(frozen=True, slots=True)
class StoredSubmission:
    """One intake row reconstructed from Gable's local database."""

    response_row_id: str
    sheet_row: int
    submitted_at: str
    intake: Intake
    content_hash: str


@dataclass(frozen=True, slots=True)
class TemplateAudit:
    """One source template Gable has adopted or measured."""

    file_id: str
    name: str
    modified_time: str
    status: str
    summary: str = ""
    slack_thread_ts: str = ""


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


def has_been_handled(connection: sqlite3.Connection, response_row_id: str) -> bool:
    """Whether polling this submission again would be unsafe or redundant.

    This is the idempotency test the poller runs on every row.

    Args:
        connection: An open connection.
        response_row_id: The submission's identity.

    Returns:
        True when a run finished, is paused for a person, or has already used
        the bounded attempt budget.

    Raises:
        sqlite3.Error: on a query failure.
    """
    suppresses_polling = TERMINAL | PAUSED
    placeholders = ",".join("?" * len(suppresses_polling))
    row = connection.execute(
        f"SELECT 1 FROM runs WHERE response_row_id = ? AND status IN ({placeholders}) LIMIT 1",
        (response_row_id, *sorted(suppresses_polling)),
    ).fetchone()
    return row is not None or run_attempt_count(connection, response_row_id) >= MAX_RUN_ATTEMPTS


def run_attempt_count(connection: sqlite3.Connection, response_row_id: str) -> int:
    """Count runs already opened for one submission.

    Args:
        connection: An open connection.
        response_row_id: The submission's identity.

    Returns:
        The number of attempts already recorded.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT COUNT(*) AS attempts FROM runs WHERE response_row_id = ?",
        (response_row_id,),
    ).fetchone()
    return int(row["attempts"])


def start_run(connection: sqlite3.Connection, response_row_id: str) -> RunRow:
    """Open a new run for a submission.

    Args:
        connection: An open connection.
        response_row_id: The submission this run is for.

    Returns:
        The new run, status `pending`.

    Raises:
        RunLimitReachedError: when three attempts already exist.
        sqlite3.Error: on a write failure, including a foreign-key violation if
            the submission was never recorded.
    """
    if run_attempt_count(connection, response_row_id) >= MAX_RUN_ATTEMPTS:
        msg = f"run attempt limit reached for {response_row_id}"
        raise RunLimitReachedError(msg)
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
        "ai_enhanced",
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
        "SELECT run_id, response_row_id, status, template_file_id, template_label,"
        " output_file_id, output_url, slack_thread_ts, failure_reason, photo_url,"
        " photo_source, ai_enhanced"
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
        "SELECT run_id, response_row_id, status, template_file_id, template_label,"
        " output_file_id, output_url, slack_thread_ts, failure_reason, photo_url,"
        " photo_source, ai_enhanced"
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
        "SELECT run_id, response_row_id, status, template_file_id, template_label,"
        " output_file_id, output_url, slack_thread_ts, failure_reason, photo_url,"
        " photo_source, ai_enhanced"
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
        output_file_id=row["output_file_id"] or "",
        output_url=row["output_url"] or "",
        slack_thread_ts=row["slack_thread_ts"] or "",
        failure_reason=row["failure_reason"] or "",
        template_file_id=row["template_file_id"] or "",
        template_label=row["template_label"] or "",
        photo_url=row["photo_url"] or "",
        photo_source=row["photo_source"] or "",
        ai_enhanced=bool(row["ai_enhanced"]),
    )


def template_catalog_adopted(connection: sqlite3.Connection) -> bool:
    """Whether the pre-existing template folder has been baselined once."""
    row = connection.execute("SELECT 1 FROM template_scan_state WHERE singleton = 1").fetchone()
    return row is not None


def adopt_template_catalog(
    connection: sqlite3.Connection,
    templates: list[tuple[str, str, str]],
) -> None:
    """Record current files without announcing them as newly uploaded."""
    now = _now()
    connection.executemany(
        """
        INSERT OR IGNORE INTO template_audits (
            file_id, name, modified_time, status, summary, checked_at
        ) VALUES (?, ?, ?, 'baseline', 'adopted before template monitoring', ?)
        """,
        [(file_id, name, modified_time, now) for file_id, name, modified_time in templates],
    )
    connection.execute(
        "INSERT OR IGNORE INTO template_scan_state (singleton, adopted_at) VALUES (1, ?)",
        (now,),
    )


def template_audit(
    connection: sqlite3.Connection,
    file_id: str,
) -> TemplateAudit | None:
    """Return the stored review for one source file, if Gable has seen it."""
    row = connection.execute(
        """
        SELECT file_id, name, modified_time, status, summary, slack_thread_ts
          FROM template_audits
         WHERE file_id = ?
        """,
        (file_id,),
    ).fetchone()
    return _to_template_audit(row) if row else None


def template_for_thread(
    connection: sqlite3.Connection,
    thread_ts: str,
) -> TemplateAudit | None:
    """Resolve a Gable template-review thread back to its Drive file."""
    row = connection.execute(
        """
        SELECT file_id, name, modified_time, status, summary, slack_thread_ts
          FROM template_audits
         WHERE slack_thread_ts = ?
         ORDER BY checked_at DESC
         LIMIT 1
        """,
        (thread_ts,),
    ).fetchone()
    return _to_template_audit(row) if row else None


def record_template_audit(
    connection: sqlite3.Connection,
    file_id: str,
    name: str,
    modified_time: str,
    status: str,
    summary: str,
    slack_thread_ts: str = "",
) -> None:
    """Upsert one measured source-template outcome and its owned thread."""
    connection.execute(
        """
        INSERT INTO template_audits (
            file_id, name, modified_time, status, summary,
            slack_thread_ts, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            name = excluded.name,
            modified_time = excluded.modified_time,
            status = excluded.status,
            summary = excluded.summary,
            slack_thread_ts = CASE
                WHEN excluded.slack_thread_ts = '' THEN template_audits.slack_thread_ts
                ELSE excluded.slack_thread_ts
            END,
            checked_at = excluded.checked_at
        """,
        (file_id, name, modified_time, status, summary, slack_thread_ts, _now()),
    )


def _to_template_audit(row: sqlite3.Row) -> TemplateAudit:
    """Turn one template audit query into its immutable record."""
    return TemplateAudit(
        file_id=str(row["file_id"]),
        name=str(row["name"]),
        modified_time=str(row["modified_time"] or ""),
        status=str(row["status"]),
        summary=str(row["summary"] or ""),
        slack_thread_ts=str(row["slack_thread_ts"] or ""),
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
