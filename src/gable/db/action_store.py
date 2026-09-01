"""Durable outbox rows for conversational actions on a run.

Split from `db.question_store` on 2026-09-01 when that module crossed the
800-line ceiling. A question or outcome is Gable speaking on its own; an
action is Gable answering a person's instruction in a thread it owns — a
photo replacement, a headshot replacement, an edit — and each is recorded
exactly once against the Slack event that asked for it, so a redelivered
event cannot repeat the mutation.

Does not handle: delivering the row to Slack, which `pipeline.questions` does
for every notification kind alike.
"""

from __future__ import annotations

import sqlite3

from gable.db.question_store import (
    _QUESTION_COLUMNS,
    PHOTO_REPLACEMENT_MESSAGE,
    PendingRunQuestion,
    _from_row,
    _now,
    _prepare_run_notification,
    _transition,
)


def prepare_run_action_notification(
    connection: sqlite3.Connection,
    run_id: str,
    action_id: str,
    message: str,
    thread_ts: str,
) -> PendingRunQuestion:
    """Persist one verified conversational mutation result exactly once."""
    clean_action_id = action_id.strip()
    if not clean_action_id:
        raise ValueError("a conversational action notification needs its Slack event id")
    existing = connection.execute(
        f"SELECT {_QUESTION_COLUMNS} FROM run_questions "
        "WHERE run_id = ? AND notification_kind = 'action' AND question_label = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (run_id, clean_action_id),
    ).fetchone()
    if existing is not None:
        return _from_row(existing)
    run = connection.execute(
        "SELECT status, slack_thread_ts FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise ValueError("the edited run no longer exists")
    root = thread_ts.strip()
    if not root or str(run["slack_thread_ts"] or "") != root:
        raise ValueError("the edited run is not owned by this Slack thread")
    status = str(run["status"])
    # Compared against the constant, never against a literal typed here.
    target_status = "needs_photo" if message.strip() == PHOTO_REPLACEMENT_MESSAGE else status
    return _prepare_run_notification(
        connection,
        run_id,
        "action",
        status,
        target_status,
        message,
        question_label=clean_action_id,
        thread_ts=root,
        confirmation_detail="Slack confirmed the conversational change outcome",
        transition_detail="verified conversational change persisted before Slack delivery",
    )


def prepare_photo_replacement_action(
    connection: sqlite3.Connection,
    run_id: str,
    action_id: str,
    thread_ts: str,
) -> PendingRunQuestion | None:
    """Atomically claim and persist one hero-replacement Slack action.

    This is the only mutating conversational action currently enabled. The
    event claim, needs_photo transition, run event, and durable instruction are
    one SQLite transaction, so duplicate deliveries on separate processes can
    neither repeat the transition nor race an answering upload.
    """
    clean_action_id = action_id.strip()
    root = thread_ts.strip()
    if not clean_action_id or not root:
        return None
    with _transition(connection):
        run = connection.execute(
            "SELECT status, slack_thread_ts FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if (
            run is None
            or str(run["status"]) not in {"delivered", "needs_review"}
            or str(run["slack_thread_ts"] or "") != root
        ):
            return None
        claimed = connection.execute(
            """
            INSERT OR IGNORE INTO slack_event_claims (
                route, event_id, subject_id, thread_ts, fingerprint, claimed_at
            ) VALUES ('run_action', ?, ?, ?, 'replace_photo:hero', ?)
            """,
            (clean_action_id, run_id, root, _now()),
        )
        if claimed.rowcount != 1:
            return None
        now = _now()
        changed = connection.execute(
            "UPDATE runs SET status = 'needs_photo', updated_at = ?, failure_reason = ? "
            "WHERE run_id = ? AND status IN ('delivered', 'needs_review')",
            (now, PHOTO_REPLACEMENT_MESSAGE, run_id),
        )
        if changed.rowcount != 1:
            raise sqlite3.IntegrityError("the photo-replacement action lost its run claim")
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, "needs_photo", "waiting for a replacement property photo"),
        )
        pending = _prepare_run_notification(
            connection,
            run_id,
            "action",
            "needs_photo",
            "needs_photo",
            PHOTO_REPLACEMENT_MESSAGE,
            question_label=clean_action_id,
            thread_ts=root,
            confirmation_detail="Slack confirmed the conversational change outcome",
            transition_detail="photo replacement instruction persisted before Slack delivery",
        )
        connection.execute(
            "UPDATE slack_event_claims SET completed_at = ?, detail = ? "
            "WHERE route = 'run_action' AND event_id = ? AND subject_id = ?",
            (now, "photo replacement instruction persisted", clean_action_id, run_id),
        )
    return pending


def prepare_headshot_replacement_action(
    connection: sqlite3.Connection,
    run_id: str,
    action_id: str,
    thread_ts: str,
    message: str,
) -> PendingRunQuestion | None:
    """Atomically claim and persist one authoritative-folder headshot request."""
    clean_action_id = action_id.strip()
    root = thread_ts.strip()
    clean_message = message.strip()
    if not clean_action_id or not root or not clean_message:
        return None
    with _transition(connection):
        run = connection.execute(
            "SELECT status, slack_thread_ts FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if (
            run is None
            or str(run["status"]) not in {"delivered", "needs_review"}
            or str(run["slack_thread_ts"] or "") != root
        ):
            return None
        claimed = connection.execute(
            """
            INSERT OR IGNORE INTO slack_event_claims (
                route, event_id, subject_id, thread_ts, fingerprint, claimed_at
            ) VALUES ('run_action', ?, ?, ?, 'replace_photo:headshot', ?)
            """,
            (clean_action_id, run_id, root, _now()),
        )
        if claimed.rowcount != 1:
            return None
        now = _now()
        changed = connection.execute(
            "UPDATE runs SET status = 'needs_info', updated_at = ?, failure_reason = ? "
            "WHERE run_id = ? AND status IN ('delivered', 'needs_review')",
            (now, clean_message[:400], run_id),
        )
        if changed.rowcount != 1:
            raise sqlite3.IntegrityError("the headshot action lost its run claim")
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, "needs_info", "waiting for an updated filed agent headshot"),
        )
        pending = _prepare_run_notification(
            connection,
            run_id,
            "action",
            "needs_info",
            "needs_info",
            clean_message,
            question_label=clean_action_id,
            thread_ts=root,
            confirmation_detail="Slack confirmed the conversational change outcome",
        )
        connection.execute(
            "UPDATE slack_event_claims SET completed_at = ?, detail = ? "
            "WHERE route = 'run_action' AND event_id = ? AND subject_id = ?",
            (now, "headshot replacement instruction persisted", clean_action_id, run_id),
        )
    return pending
