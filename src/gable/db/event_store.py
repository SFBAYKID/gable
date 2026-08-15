"""Durably suppress replay of user-triggered Slack work across restarts."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class AbandonedSlackEvent:
    """One accepted Slack event whose handler never stored an outcome."""

    route: str
    event_id: str
    subject_id: str
    thread_ts: str
    fingerprint: str
    claimed_at: str


def abandoned_slack_events(
    connection: sqlite3.Connection,
    route: str,
) -> tuple[AbandonedSlackEvent, ...]:
    """Return claims in one route that were accepted but never completed.

    A row is only written before the work it guards and only completed after
    that work's outcome is durable, so an incomplete row means the process died
    mid-handler. The caller decides whether that work is safe to resume or must
    be reported and released.

    Args:
        connection: An open connection.
        route: The handler family, for example ``file_share``.

    Returns:
        Every incomplete claim, oldest first.

    Raises:
        sqlite3.Error: on a query failure.
    """
    rows = connection.execute(
        "SELECT route, event_id, subject_id, thread_ts, fingerprint, claimed_at "
        "FROM slack_event_claims WHERE route = ? AND completed_at = '' "
        "ORDER BY claimed_at, rowid",
        (route.strip(),),
    ).fetchall()
    return tuple(
        AbandonedSlackEvent(
            route=str(row["route"]),
            event_id=str(row["event_id"]),
            subject_id=str(row["subject_id"]),
            thread_ts=str(row["thread_ts"]),
            fingerprint=str(row["fingerprint"]),
            claimed_at=str(row["claimed_at"]),
        )
        for row in rows
    )


def has_open_slack_event(
    connection: sqlite3.Connection,
    route: str,
    subject_id: str,
) -> bool:
    """Whether one subject has an accepted-but-unfinished claim in a route.

    An open ``file_share`` claim means an upload for this run has been accepted
    and its outcome is not yet durable — the handler is between download and
    resume. A text-triggered rebuild that claims the run inside that window
    wins the paused-run claim (the photo question is already satisfied), builds
    without the new photograph, and the upload's own resume then loses and
    marks its ingress complete — dropping the photo that was just sent. The
    rebuild path consults this before claiming, and waits its turn instead.

    Args:
        connection: An open connection.
        route: The handler family, for example ``file_share``.
        subject_id: The run the work belongs to.

    Returns:
        True while such a claim is open.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT 1 FROM slack_event_claims "
        "WHERE route = ? AND subject_id = ? AND completed_at = '' LIMIT 1",
        (route.strip(), subject_id.strip()),
    ).fetchone()
    return row is not None


def claim_slack_event(
    connection: sqlite3.Connection,
    route: str,
    event_id: str,
    subject_id: str,
    thread_ts: str,
    fingerprint: str,
) -> bool:
    """Claim one stable Slack event before work that must never be repeated.

    The primary key is the Slack event identity within its handler family. A
    crash leaves the row claimed deliberately: without a transactional boundary
    to Slack, Slides, Drive, or a paid model, automatically replaying it could
    repeat a mutation or spend. A new human message has a new event id and can
    retry explicitly.
    """
    clean = tuple(value.strip() for value in (route, event_id, subject_id, thread_ts))
    if not all(clean):
        return False
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO slack_event_claims (
            route, event_id, subject_id, thread_ts, fingerprint, claimed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (*clean, fingerprint[:400], _now()),
    )
    return cursor.rowcount == 1


def complete_slack_event(
    connection: sqlite3.Connection,
    route: str,
    event_id: str,
    subject_id: str,
    detail: str,
) -> bool:
    """Mark only the matching durable claim complete after its result is stored."""
    cursor = connection.execute(
        """
        UPDATE slack_event_claims
           SET completed_at = ?, detail = ?
         WHERE route = ? AND event_id = ? AND subject_id = ?
           AND completed_at = ''
        """,
        (_now(), detail[:400], route.strip(), event_id.strip(), subject_id.strip()),
    )
    return cursor.rowcount == 1


def slack_event_claimed(
    connection: sqlite3.Connection,
    route: str,
    event_id: str,
) -> bool:
    """Return whether this process or an earlier one already accepted the event."""
    row = connection.execute(
        "SELECT 1 FROM slack_event_claims WHERE route = ? AND event_id = ?",
        (route.strip(), event_id.strip()),
    ).fetchone()
    return row is not None
