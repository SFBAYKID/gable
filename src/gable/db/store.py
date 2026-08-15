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
from typing import Final

from gable.db.event_store import (
    AbandonedSlackEvent,
    abandoned_slack_events,
    claim_slack_event,
    complete_slack_event,
    has_open_slack_event,
    slack_event_claimed,
)
from gable.db.photo_store import (
    claim_run_for_photo,
    has_pending_photo_question,
    satisfy_pending_photo_question,
)
from gable.db.question_store import (
    QUESTION_NOTIFICATION_PENDING,
    PendingRunQuestion,
    adopt_run_thread_for_notification,
    bind_run_question_thread,
    claim_run_notification_delivery,
    confirm_run_question,
    has_pending_run_notification,
    has_run_action_notification,
    pending_run_question,
    pending_run_questions,
    prepare_headshot_replacement_action,
    prepare_photo_replacement_action,
    prepare_run_action_notification,
    prepare_run_outcome,
    prepare_run_question,
    release_run_notification_delivery,
)
from gable.db.run_store import (
    ACTIVE,
    BUILD_WITH_BLANKS,
    INTERRUPTED_NOTIFICATION_PENDING,
    INTERRUPTED_REASON,
    MAX_RUN_ATTEMPTS,
    PAUSED,
    PHOTO_REPLACEMENT_WAITING,
    TERMINAL,
    VALID_STATUSES,
    RunAlreadyActiveError,
    RunLimitReachedError,
    RunRow,
    acknowledge_interrupted_run,
    approve_blank_fields,
    blanks_approved,
    claim_paused_run,
    latest_run,
    pending_interrupted_runs,
    recover_interrupted_runs,
    reopen_for_rebuild,
    run_attempt_count,
    run_by_id,
    run_for_thread,
    set_status,
    start_run,
)
from gable.db.template_store import (
    TemplateAudit,
    adopt_template_catalog,
    claim_template_notification_delivery,
    confirm_template_notification,
    pending_template_notifications,
    record_template_audit,
    release_template_notification_delivery,
    template_audit,
    template_catalog_adopted,
    template_for_thread,
)
from gable.listings.address import tidy as tidy_address
from gable.listings.intake import Intake, address_looks_usable

__all__ = [
    "ACTIVE",
    "BUILD_WITH_BLANKS",
    "INTERRUPTED_NOTIFICATION_PENDING",
    "INTERRUPTED_REASON",
    "MAX_RUN_ATTEMPTS",
    "PAUSED",
    "PHOTO_REPLACEMENT_WAITING",
    "QUESTION_NOTIFICATION_PENDING",
    "TERMINAL",
    "VALID_STATUSES",
    "AbandonedSlackEvent",
    "PendingRunQuestion",
    "RunAlreadyActiveError",
    "RunLimitReachedError",
    "RunRow",
    "TemplateAudit",
    "abandoned_slack_events",
    "acknowledge_interrupted_run",
    "adopt_run_thread_for_notification",
    "adopt_template_catalog",
    "approve_blank_fields",
    "bind_run_question_thread",
    "blanks_approved",
    "claim_paused_run",
    "claim_run_for_photo",
    "claim_run_notification_delivery",
    "claim_slack_event",
    "claim_template_notification_delivery",
    "complete_slack_event",
    "confirm_run_question",
    "confirm_template_notification",
    "has_open_slack_event",
    "has_pending_photo_question",
    "has_pending_run_notification",
    "has_run_action_notification",
    "latest_run",
    "pending_interrupted_runs",
    "pending_run_question",
    "pending_run_questions",
    "pending_template_notifications",
    "prepare_headshot_replacement_action",
    "prepare_photo_replacement_action",
    "prepare_run_action_notification",
    "prepare_run_outcome",
    "prepare_run_question",
    "record_template_audit",
    "recover_interrupted_runs",
    "release_run_notification_delivery",
    "release_template_notification_delivery",
    "reopen_for_rebuild",
    "run_attempt_count",
    "run_by_id",
    "run_for_thread",
    "satisfy_pending_photo_question",
    "set_status",
    "slack_event_claimed",
    "start_run",
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
    source_tab: str = ""


def record_submission(
    connection: sqlite3.Connection,
    response_row_id: str,
    sheet_row: int,
    submitted_at: str,
    intake: Intake,
    content_hash: str = "",
    source_tab: str = "",
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
        source_tab: Exact read-only form tab the row came from.

    Returns:
        True if this was the first time seeing it, False if already known.

    Raises:
        sqlite3.Error: on a write failure.
    """
    existing = connection.execute(
        "SELECT sheet_row, submitted_at, content_hash, source_tab "
        "FROM submissions WHERE response_row_id = ?",
        (response_row_id,),
    ).fetchone()
    if existing:
        # Any answer can be corrected in place while the form timestamp keeps
        # the same stable identity. Keep the stored payload current so a paused
        # resume or explicit retry does not rebuild from stale form data.
        # Ordinary polling remains suppressed by the existing run state; this
        # update alone never opens an attempt.
        content_changed = content_hash and str(existing["content_hash"] or "") != content_hash
        clean_tab = source_tab.strip()
        tab_changed = bool(clean_tab) and str(existing["source_tab"] or "") != clean_tab
        location_changed = int(existing["sheet_row"]) != sheet_row
        timestamp_changed = str(existing["submitted_at"] or "") != submitted_at
        if content_changed or tab_changed or location_changed or timestamp_changed:
            connection.execute(
                """
                UPDATE submissions
                   SET sheet_row = ?, submitted_at = ?, agent_email = ?, agent_name = ?,
                       request_type = ?, address = ?, post_details = ?, open_house = ?,
                       new_price = ?, closing_price = ?, extra_notes = ?, side = ?,
                       notes = ?, content_hash = ?, source_tab = ?
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
                    content_hash or str(existing["content_hash"] or ""),
                    clean_tab or str(existing["source_tab"] or ""),
                    response_row_id,
                ),
            )
        return False
    connection.execute(
        """
        INSERT INTO submissions (
            response_row_id, sheet_row, submitted_at, agent_email, agent_name,
            request_type, address, post_details, open_house, new_price,
            closing_price, extra_notes, side, notes, first_seen_at, content_hash,
            source_tab
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            source_tab.strip(),
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
               closing_price, extra_notes, side, notes, content_hash, source_tab
          FROM submissions
         WHERE response_row_id = ?
        """,
        (response_row_id,),
    ).fetchone()
    if not row:
        return None
    # An address a person gave outranks the one on the form, because Gable only
    # ever asks when the form's own value cannot be read. Laid over the stored
    # row rather than written into it, so re-reading the sheet — which every
    # resume does — cannot quietly undo the correction.
    stated = connection.execute(
        "SELECT address FROM stated_addresses WHERE response_row_id = ?",
        (response_row_id,),
    ).fetchone()
    return StoredSubmission(
        response_row_id=row["response_row_id"],
        sheet_row=int(row["sheet_row"]),
        submitted_at=row["submitted_at"],
        intake=Intake(
            agent_email=row["agent_email"],
            agent_name=row["agent_name"],
            request_type=row["request_type"],
            address=str(stated["address"]) if stated else row["address"],
            post_details=row["post_details"],
            open_house=row["open_house"],
            new_price=row["new_price"],
            closing_price=row["closing_price"],
            extra_notes=row["extra_notes"],
            side=row["side"],
            notes=row["notes"],
        ),
        content_hash=row["content_hash"],
        source_tab=str(row["source_tab"] or ""),
    )


def has_been_handled(connection: sqlite3.Connection, response_row_id: str) -> bool:
    """Whether polling this submission again would be unsafe or redundant.

    This is the idempotency test the poller runs on every row.

    Args:
        connection: An open connection.
        response_row_id: The submission's identity.

    Returns:
        True after any attempt exists. Scheduled polling starts only genuinely
        unseen rows; human-paused work resumes the same run only from its owned
        Slack thread, and failed work never restarts silently.

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


#: The facts a person may state in Slack when Gable asks for them. Restricted to
#: what a design displays and research can fail to find; a price that is not a
#: public list price still comes only from its own form column.
SUPPLIABLE_FIELDS: Final[frozenset[str]] = frozenset(
    # `review_quote` is here for the same reason the others are: Gable stops,
    # a person answers, and the answer has to be usable. Every real client
    # review on the form is 400-1000 characters and the design's quote panel is
    # drawn for about 280, so the fitter shrank Rob Morgan's below the
    # readability floor and the rendered inspection correctly refused it. The
    # shorter pull-quote a person sends back is what gets set.
    # `client_name` for the same reason: the design sets the reviewer's name
    # under the quote, the parser only reads a name written on its own line,
    # and a Zillow export writes "7/20/2026 • j E". Gable asks who said it; the
    # answer has to be recordable or the question is another dead end.
    {"beds", "baths", "square_feet", "list_price", "open_house", "review_quote", "client_name"}
)


def remember_stated_address(
    connection: sqlite3.Connection,
    response_row_id: str,
    address: str,
    stated_by: str = "",
) -> None:
    """Record the address a person gave for a submission Gable could not read.

    Args:
        connection: An open connection.
        response_row_id: The submission the correction belongs to.
        address: The address as stated.
        stated_by: The Slack user id, for the audit trail.

    Raises:
        ValueError: When the address is blank, or still not one a flyer could
            carry. Storing an unusable correction would only reproduce the same
            pause with a value nobody can trace to the form.
        sqlite3.Error: on a write failure.
    """
    tidied = tidy_address(address)
    if not tidied.strip():
        msg = "a stated address cannot be blank"
        raise ValueError(msg)
    if not address_looks_usable(tidied):
        msg = f"{address!r} is still not a usable property address"
        raise ValueError(msg)
    connection.execute(
        """
        INSERT INTO stated_addresses (response_row_id, address, stated_by, stated_at)
        VALUES (?,?,?,?)
        ON CONFLICT(response_row_id) DO UPDATE SET
            address=excluded.address, stated_by=excluded.stated_by,
            stated_at=excluded.stated_at
        """,
        (response_row_id, tidied.strip(), stated_by, _now()),
    )
    connection.commit()


def remember_supplied_fact(
    connection: sqlite3.Connection,
    address: str,
    field: str,
    value: str,
    supplied_by: str = "",
) -> None:
    """Record a fact a person stated, as distinct from one that was looked up.

    Args:
        connection: An open connection.
        address: The property address the fact is about.
        field: One of `SUPPLIABLE_FIELDS`.
        value: What was stated, kept verbatim.
        supplied_by: The Slack user id, for the audit trail.

    Raises:
        ValueError: If the field is not one a person may supply. Guessing at an
            unknown field would put an unchecked string on a client-facing
            flyer.
        sqlite3.Error: on a write failure.
    """
    if field not in SUPPLIABLE_FIELDS:
        msg = f"{field!r} is not a fact a person may supply"
        raise ValueError(msg)
    if not value.strip():
        msg = "a supplied fact cannot be blank"
        raise ValueError(msg)
    connection.execute(
        """
        INSERT INTO supplied_facts (address_key, field, value, supplied_by, supplied_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(address_key, field) DO UPDATE SET
            value=excluded.value, supplied_by=excluded.supplied_by,
            supplied_at=excluded.supplied_at
        """,
        (address_key(address), field, value.strip(), supplied_by, _now()),
    )
    connection.commit()


def recall_supplied_facts(connection: sqlite3.Connection, address: str) -> dict[str, str]:
    """Every fact a person has stated about this property.

    Args:
        connection: An open connection.
        address: The property address.

    Returns:
        Field to stated value, empty when nobody has answered for this address.

    Raises:
        sqlite3.Error: on a query failure.
    """
    rows = connection.execute(
        "SELECT field, value FROM supplied_facts WHERE address_key = ?",
        (address_key(address),),
    ).fetchall()
    return {str(row["field"]): str(row["value"]) for row in rows if str(row["value"]).strip()}
