"""Persist source-template catalogue adoption and triage verdicts.

Template audits are independent of listing-run transitions: one Drive file may
be checked before it is used by any listing, and its Slack thread identifies the
source design rather than an output flyer. Keeping that persistence here makes
the scan-once and same-thread recheck contract reviewable as one small unit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


def _now() -> str:
    """Return the UTC timestamp format used by template-audit writes."""
    return datetime.now(UTC).isoformat()


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
               notification_pending
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
               notification_pending
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
    *,
    notification_pending: bool = False,
) -> None:
    """Upsert one measured source-template outcome and its owned thread."""
    connection.execute(
        """
        INSERT INTO template_audits (
            file_id, name, modified_time, status, summary,
            slack_thread_ts, checked_at, notification_pending
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            name = excluded.name,
            modified_time = excluded.modified_time,
            status = excluded.status,
            summary = excluded.summary,
            slack_thread_ts = CASE
                WHEN excluded.slack_thread_ts = '' THEN template_audits.slack_thread_ts
                ELSE excluded.slack_thread_ts
            END,
            notification_pending = excluded.notification_pending,
            checked_at = excluded.checked_at
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
        ),
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
        notification_pending=bool(row["notification_pending"]),
    )
