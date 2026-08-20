"""Atomic bridge from a durable photo request to its answering upload."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast

from gable.db.question_store import QUESTION_NOTIFICATION_PENDING
from gable.db.run_store import claim_paused_run


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _transition(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("SAVEPOINT gable_photo_transition")
    try:
        yield
    except Exception:
        connection.execute("ROLLBACK TO gable_photo_transition")
        connection.execute("RELEASE gable_photo_transition")
        raise
    connection.execute("RELEASE gable_photo_transition")


def _pending_photo_row(
    connection: sqlite3.Connection,
    run_id: str,
    thread_ts: str,
    columns: str,
) -> sqlite3.Row | None:
    return cast(
        sqlite3.Row | None,
        connection.execute(
            f"SELECT {columns} FROM run_questions q JOIN runs r ON r.run_id = q.run_id "
            # `target_status` is where the ask PARKS the run, and a batched ask
            # that names both a design to widen and the photograph parks in
            # needs_template. Matching on it alone lost Carmen's upload during
            # the acknowledgement gap: no row matched, the claim fell through to
            # `claim_paused_run`, which refuses while an unconfirmed question
            # exists, and she was told the listing was "already being
            # rechecked". `runs.awaiting_photo` is the ask itself.
            "WHERE q.run_id = ? AND (q.target_status = 'needs_photo' OR r.awaiting_photo = 1) "
            "AND q.thread_ts = ? AND q.confirmed_at = '' AND q.satisfied_at = '' "
            "AND ((r.status = 'needs_review' AND r.failure_reason = ?) "
            "OR (r.status = 'needs_photo' AND q.notification_kind = 'action')) "
            "AND r.slack_thread_ts = ?",
            (run_id, thread_ts, QUESTION_NOTIFICATION_PENDING, thread_ts),
        ).fetchone(),
    )


def has_pending_photo_question(
    connection: sqlite3.Connection,
    run_id: str,
    thread_ts: str,
) -> bool:
    """Whether this owned thread has an unanswered, unconfirmed photo request."""
    root = thread_ts.strip()
    return bool(root and _pending_photo_row(connection, run_id, root, "1"))


def satisfy_pending_photo_question(
    connection: sqlite3.Connection,
    run_id: str,
    thread_ts: str,
) -> bool:
    """Resolve a pending request before slow file work, retaining needs_photo."""
    root = thread_ts.strip()
    if not root:
        return False
    row = _pending_photo_row(
        connection,
        run_id,
        root,
        "q.question_id, q.message, q.confirmed_reason",
    )
    if row is None:
        return _run_waiting_for_photo(connection, run_id, root)
    now = _now()
    try:
        with _transition(connection):
            resolved = connection.execute(
                "UPDATE run_questions SET satisfied_at = ?, satisfaction_detail = ? "
                "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = ''",
                (
                    now,
                    "the requested property image arrived in the owned Slack thread",
                    str(row["question_id"]),
                ),
            )
            if resolved.rowcount != 1:
                raise _PhotoClaimLostError
            restored = connection.execute(
                "UPDATE runs SET status = 'needs_photo', updated_at = ?, failure_reason = ? "
                "WHERE run_id = ? AND ((status = 'needs_review' AND failure_reason = ?) "
                "OR status = 'needs_photo') AND slack_thread_ts = ?",
                (
                    now,
                    str(row["confirmed_reason"] or row["message"])[:400],
                    run_id,
                    QUESTION_NOTIFICATION_PENDING,
                    root,
                ),
            )
            if restored.rowcount != 1:
                raise _PhotoClaimLostError
            connection.execute(
                "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
                (
                    run_id,
                    now,
                    "needs_photo",
                    "pending photo question satisfied by an owned-thread upload",
                ),
            )
    except _PhotoClaimLostError:
        return _run_waiting_for_photo(connection, run_id, root)
    return True


def _run_waiting_for_photo(
    connection: sqlite3.Connection,
    run_id: str,
    thread_ts: str,
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM runs WHERE run_id = ? AND status = 'needs_photo' "
        "AND slack_thread_ts = ? LIMIT 1",
        (run_id, thread_ts),
    ).fetchone()
    return row is not None


class _PhotoClaimLostError(RuntimeError):
    """Rollback signal for a competing question delivery or upload."""


def claim_run_for_photo(
    connection: sqlite3.Connection,
    run_id: str,
    thread_ts: str,
    fields: Mapping[str, str | int] | None = None,
    expected_status: str = "needs_photo",
) -> bool:
    """Atomically claim a confirmed photo wait or its unresolved request.

    Args:
        connection: An open connection.
        run_id: The paused run this upload belongs to.
        thread_ts: The owned thread the upload arrived in.
        fields: Photo provenance committed with the claim.
        expected_status: The paused state the caller saw when it accepted the
            upload. Defaults to `needs_photo`, which is the ordinary case: the
            run asked for a photograph and one arrived. A run whose flyer was
            built and parked in review passes `needs_review`, because a photo
            the visual gate rejected is replaced from there and pinning this to
            `needs_photo` made that claim fail after the upload was stored.

    Returns:
        True when this call owns the run.

    Raises:
        sqlite3.Error: on a write failure.
    """
    root = thread_ts.strip()
    if not root:
        return False
    row = _pending_photo_row(connection, run_id, root, "q.question_id, q.notification_kind")
    if row is None:
        return claim_paused_run(
            connection,
            run_id,
            fields,
            expected_status=expected_status,
        )
    now = _now()
    try:
        with _transition(connection):
            resolved = connection.execute(
                "UPDATE run_questions SET satisfied_at = ?, satisfaction_detail = ? "
                "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = ''",
                (
                    now,
                    "the requested property image arrived in the owned Slack thread",
                    str(row["question_id"]),
                ),
            )
            if resolved.rowcount != 1:
                raise _PhotoClaimLostError
            expected = (
                "needs_photo" if str(row["notification_kind"]) == "action" else "needs_review"
            )
            if not claim_paused_run(
                connection,
                run_id,
                fields,
                detail="pending photo question satisfied by an owned-thread upload",
                expected_status=expected,
            ):
                raise _PhotoClaimLostError
    except _PhotoClaimLostError:
        return claim_paused_run(
            connection,
            run_id,
            fields,
            expected_status="needs_photo",
        )
    return True
