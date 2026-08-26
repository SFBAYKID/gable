"""Persist source-template catalogue adoption and triage verdicts.

Template audits are independent of listing-run transitions: one Drive file may
be checked before it is used by any listing, and its Slack thread identifies the
source design rather than an output flyer. Keeping that persistence here makes
the scan-once and same-thread recheck contract reviewable as one small unit.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final


def _now() -> str:
    """Return the UTC timestamp format used by template-audit writes."""
    return datetime.now(UTC).isoformat()


#: Why a refused source template is refused. `VISUAL` is the only kind a
#: listing is allowed to build through -- see `pipeline.placement.template_clearance`
#: and migration 15. Everything else makes the file unusable rather than
#: merely questionable, so it stops a listing as well as a design thread.
BLOCKER_STRUCTURAL: Final[str] = "structural"
BLOCKER_VISUAL: Final[str] = "visual"
BLOCKER_UNSUPPORTED: Final[str] = "unsupported"
BLOCKER_DUPLICATE: Final[str] = "duplicate"
BLOCKER_MISSING: Final[str] = "missing"


@dataclass(frozen=True, slots=True)
class TemplateAudit:
    """One source template Gable has adopted or measured."""

    file_id: str
    name: str
    modified_time: str
    status: str
    summary: str = ""
    slack_thread_ts: str = ""
    notification_pending: bool = False
    checked_at: str = ""
    delivery_claim_token: str = ""
    delivery_claimed_at: str = ""
    notification_attempted_at: str = ""
    notification_attempt_count: int = 0
    blocker_kind: str = ""


def template_catalog_adopted(connection: sqlite3.Connection) -> bool:
    """Return whether the pre-existing template folder was baselined once."""
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
        SELECT file_id, name, modified_time, status, summary, slack_thread_ts,
               notification_pending, checked_at, delivery_claim_token,
               delivery_claimed_at, notification_attempted_at,
               notification_attempt_count, blocker_kind
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
        SELECT file_id, name, modified_time, status, summary, slack_thread_ts,
               notification_pending, checked_at, delivery_claim_token,
               delivery_claimed_at, notification_attempted_at,
               notification_attempt_count, blocker_kind
          FROM template_audits
         WHERE slack_thread_ts = ?
         ORDER BY checked_at DESC
         LIMIT 1
        """,
        (thread_ts,),
    ).fetchone()
    return _to_template_audit(row) if row else None


def pending_template_notifications(connection: sqlite3.Connection) -> tuple[TemplateAudit, ...]:
    """Return exact stored template verdicts whose Slack delivery is unresolved."""
    rows = connection.execute(
        """
        SELECT file_id, name, modified_time, status, summary, slack_thread_ts,
               notification_pending, checked_at, delivery_claim_token,
               delivery_claimed_at, notification_attempted_at,
               notification_attempt_count, blocker_kind
          FROM template_audits
         WHERE notification_pending = 1
           AND status != 'baseline'
           AND summary != ''
         ORDER BY checked_at, file_id
        """
    ).fetchall()
    return tuple(_to_template_audit(row) for row in rows)


def claim_template_notification_delivery(
    connection: sqlite3.Connection,
    audit: TemplateAudit,
    expected_attempt_count: int,
    stale_before: str,
) -> str:
    """Atomically mark and reserve the template verdict's sole Slack write."""
    token = str(uuid.uuid4())
    now = _now()
    cursor = connection.execute(
        """
        UPDATE template_audits
           SET delivery_claim_token = ?, delivery_claimed_at = ?,
               notification_attempted_at = ?,
               notification_attempt_count = notification_attempt_count + 1
         WHERE file_id = ? AND modified_time = ? AND summary = ? AND checked_at = ?
           AND notification_pending = 1
           AND notification_attempt_count = ?
           AND (delivery_claim_token = '' OR delivery_claimed_at <= ?)
        """,
        (
            token,
            now,
            now,
            audit.file_id,
            audit.modified_time,
            audit.summary,
            audit.checked_at,
            expected_attempt_count,
            stale_before,
        ),
    )
    return token if cursor.rowcount == 1 else ""


def release_template_notification_delivery(
    connection: sqlite3.Connection,
    audit: TemplateAudit,
    claim_token: str,
) -> None:
    """Release this process's active claim without permitting another write."""
    connection.execute(
        "UPDATE template_audits SET delivery_claim_token = '', delivery_claimed_at = '' "
        "WHERE file_id = ? AND modified_time = ? AND checked_at = ? "
        "AND delivery_claim_token = ?",
        (audit.file_id, audit.modified_time, audit.checked_at, claim_token),
    )


def confirm_template_notification(
    connection: sqlite3.Connection,
    audit: TemplateAudit,
    posted_ts: str,
) -> bool:
    """Confirm only the exact still-pending revision that Slack acknowledged."""
    clean_ts = posted_ts.strip()
    if not clean_ts:
        return False
    cursor = connection.execute(
        """
        UPDATE template_audits
           SET slack_thread_ts = CASE
                   WHEN slack_thread_ts = '' THEN ?
                   ELSE slack_thread_ts
               END,
               notification_pending = 0,
               delivery_claim_token = '',
               delivery_claimed_at = ''
         WHERE file_id = ?
           AND modified_time = ?
           AND summary = ?
           AND checked_at = ?
           AND notification_pending = 1
        """,
        (
            clean_ts,
            audit.file_id,
            audit.modified_time,
            audit.summary,
            audit.checked_at,
        ),
    )
    return cursor.rowcount == 1


def record_template_audit(
    connection: sqlite3.Connection,
    file_id: str,
    name: str,
    modified_time: str,
    status: str,
    summary: str,
    slack_thread_ts: str = "",
    *,
    notification_pending: bool = False,
    blocker_kind: str = "",
) -> bool:
    """Upsert one measured source-template outcome and its owned thread.

    Args:
        connection: Open database connection.
        file_id: Drive id of the source design.
        name: Its current human-visible file name.
        modified_time: The Drive revision this verdict measured.
        status: `baseline`, `ready`, or `needs_template`.
        summary: Exactly what Gable will say about it, or empty to stay quiet.
        slack_thread_ts: The design's owned thread; empty keeps the stored one.
        notification_pending: Whether this verdict still owes a Slack post.
        blocker_kind: One of the `BLOCKER_*` names when `status` refuses the
            design, empty otherwise. A listing builds through `visual` alone;
            see `pipeline.placement.template_clearance`.

    Returns:
        True when this call wrote the row, False when a pending or claimed
        delivery held it.

    Raises:
        sqlite3.Error: If the upsert cannot run.
    """
    cursor = connection.execute(
        """
        INSERT INTO template_audits (
            file_id, name, modified_time, status, summary,
            slack_thread_ts, checked_at, notification_pending, blocker_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            name = excluded.name,
            modified_time = excluded.modified_time,
            status = excluded.status,
            summary = excluded.summary,
            blocker_kind = excluded.blocker_kind,
            slack_thread_ts = CASE
                WHEN excluded.slack_thread_ts = '' THEN template_audits.slack_thread_ts
                ELSE excluded.slack_thread_ts
            END,
            notification_pending = excluded.notification_pending,
            delivery_claim_token = '',
            delivery_claimed_at = '',
            notification_attempted_at = '',
            notification_attempt_count = 0,
            checked_at = excluded.checked_at
        WHERE template_audits.notification_pending = 0
          AND template_audits.delivery_claim_token = ''
        """,
        (
            file_id,
            name,
            modified_time,
            status,
            summary,
            slack_thread_ts,
            _now(),
            int(notification_pending),
            blocker_kind,
        ),
    )
    return cursor.rowcount == 1


def _to_template_audit(row: sqlite3.Row) -> TemplateAudit:
    """Turn one template audit query into its immutable record."""
    return TemplateAudit(
        file_id=str(row["file_id"]),
        name=str(row["name"]),
        modified_time=str(row["modified_time"] or ""),
        status=str(row["status"]),
        summary=str(row["summary"] or ""),
        slack_thread_ts=str(row["slack_thread_ts"] or ""),
        notification_pending=bool(row["notification_pending"]),
        checked_at=str(row["checked_at"] or ""),
        delivery_claim_token=str(row["delivery_claim_token"] or ""),
        delivery_claimed_at=str(row["delivery_claimed_at"] or ""),
        notification_attempted_at=str(row["notification_attempted_at"] or ""),
        notification_attempt_count=int(row["notification_attempt_count"]),
        blocker_kind=str(row["blocker_kind"] or ""),
    )
