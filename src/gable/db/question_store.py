"""Durable Slack-notification outbox for run questions and outcomes.

A database transition and a Slack message cannot share one transaction. This
module records exact questions and outcomes first, keeps the run in its truthful
pending state, and advances only after Slack returns the message timestamp.
Rows survive restart and carry stable client ids, so every attempt is the same
Slack message as well as the same SQLite transition.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from gable.db.run_store import (
    INTERRUPTED_NOTIFICATION_PENDING,
    PAUSED,
    VALID_STATUSES,
)

SLACK_NOTIFICATION_PENDING: Final[str] = "a Slack notification is still waiting to be delivered"
# Compatibility name for callers that specifically inspect a pending question.
QUESTION_NOTIFICATION_PENDING: Final[str] = SLACK_NOTIFICATION_PENDING

#: The exact words that also mean "this run is now waiting for a photograph".
#: Named because the state used to be decided by comparing the message text
#: inline: reword it for voice and the run would silently stop moving to
#: `needs_photo`, with nothing to point at. A constant cannot drift from
#: itself, and the three places that need these words now share one.
PHOTO_REPLACEMENT_MESSAGE: Final[str] = "Send me the new property photo."


def _now() -> str:
    """Return the timestamp format shared by run persistence."""
    return datetime.now(UTC).isoformat()


@contextmanager
def _transition(connection: sqlite3.Connection) -> Iterator[None]:
    """Commit an outbox mutation and its matching run event together."""
    connection.execute("SAVEPOINT gable_question_transition")
    try:
        yield
    except Exception:
        connection.execute("ROLLBACK TO gable_question_transition")
        connection.execute("RELEASE gable_question_transition")
        raise
    connection.execute("RELEASE gable_question_transition")


@dataclass(frozen=True, slots=True)
class PendingRunQuestion:
    """One exact Slack notification whose confirmation is still owed."""

    question_id: str
    run_id: str
    notification_kind: str
    pending_status: str
    target_status: str
    message: str
    question_label: str
    headline: str
    thread_ts: str
    headline_ts: str
    headline_client_id: str
    question_client_id: str
    confirmed_reason: str
    confirmation_detail: str
    created_at: str
    delivery_claim_token: str
    delivery_claimed_at: str
    headline_attempted_at: str
    headline_attempt_count: int
    question_attempted_at: str
    question_attempt_count: int


_QUESTION_COLUMNS: Final[str] = (
    "question_id, run_id, notification_kind, pending_status, target_status, "
    "message, question_label, headline, "
    "thread_ts, headline_ts, headline_client_id, question_client_id, "
    "confirmed_reason, confirmation_detail, created_at, delivery_claim_token, "
    "delivery_claimed_at, headline_attempted_at, headline_attempt_count, "
    "question_attempted_at, question_attempt_count"
)


def _from_row(row: sqlite3.Row) -> PendingRunQuestion:
    """Convert one query row into the typed outbox boundary."""
    return PendingRunQuestion(
        question_id=str(row["question_id"]),
        run_id=str(row["run_id"]),
        notification_kind=str(row["notification_kind"]),
        pending_status=str(row["pending_status"]),
        target_status=str(row["target_status"]),
        message=str(row["message"]),
        question_label=str(row["question_label"]),
        headline=str(row["headline"]),
        thread_ts=str(row["thread_ts"]),
        headline_ts=str(row["headline_ts"]),
        headline_client_id=str(row["headline_client_id"]),
        question_client_id=str(row["question_client_id"]),
        confirmed_reason=str(row["confirmed_reason"]),
        confirmation_detail=str(row["confirmation_detail"]),
        created_at=str(row["created_at"]),
        delivery_claim_token=str(row["delivery_claim_token"]),
        delivery_claimed_at=str(row["delivery_claimed_at"]),
        headline_attempted_at=str(row["headline_attempted_at"]),
        headline_attempt_count=int(row["headline_attempt_count"]),
        question_attempted_at=str(row["question_attempted_at"]),
        question_attempt_count=int(row["question_attempt_count"]),
    )


def prepare_run_question(
    connection: sqlite3.Connection,
    run_id: str,
    target_status: str,
    message: str,
    *,
    question_label: str = "",
    headline: str = "",
    thread_ts: str = "",
    confirmed_reason: str = "",
    confirmation_detail: str = "",
    transition_detail: str = "Slack question persisted before delivery",
    output_file_id: str = "",
    output_url: str = "",
) -> PendingRunQuestion:
    """Persist an exact question before attempting either Slack message."""
    if target_status not in PAUSED:
        raise ValueError(f"question target is not paused: {target_status!r}")
    return _prepare_run_notification(
        connection,
        run_id,
        "question",
        "needs_review",
        target_status,
        message,
        question_label=question_label,
        headline=headline,
        thread_ts=thread_ts,
        confirmed_reason=confirmed_reason or message,
        confirmation_detail=(
            confirmation_detail or f"Slack confirmed the question for {target_status}"
        ),
        transition_detail=transition_detail,
        output_file_id=output_file_id,
        output_url=output_url,
    )


def prepare_run_outcome(
    connection: sqlite3.Connection,
    run_id: str,
    target_status: str,
    message: str,
    *,
    pending_status: str | None = None,
    thread_ts: str = "",
    confirmed_reason: str = "",
    confirmation_detail: str = "Slack confirmed the run outcome",
    transition_detail: str = "Slack outcome persisted before delivery",
    output_file_id: str = "",
    output_url: str = "",
) -> PendingRunQuestion:
    """Persist one truthful review or terminal outcome before posting it."""
    waiting_status = pending_status or target_status
    if target_status not in VALID_STATUSES or waiting_status not in VALID_STATUSES:
        raise ValueError("outcome status is not part of the run state machine")
    return _prepare_run_notification(
        connection,
        run_id,
        "outcome",
        waiting_status,
        target_status,
        message,
        thread_ts=thread_ts,
        confirmed_reason=confirmed_reason,
        confirmation_detail=confirmation_detail,
        transition_detail=transition_detail,
        output_file_id=output_file_id,
        output_url=output_url,
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


def _prepare_run_notification(
    connection: sqlite3.Connection,
    run_id: str,
    notification_kind: str,
    pending_status: str,
    target_status: str,
    message: str,
    *,
    question_label: str = "",
    headline: str = "",
    thread_ts: str = "",
    confirmed_reason: str = "",
    confirmation_detail: str = "",
    transition_detail: str = "Slack notification persisted before delivery",
    output_file_id: str = "",
    output_url: str = "",
) -> PendingRunQuestion:
    """Atomically persist an exact message and its truthful waiting state."""
    clean_message = message.strip()
    clean_label = (
        (question_label or clean_message).strip()
        if notification_kind in {"question", "action"}
        else ""
    )
    clean_headline = headline.strip()
    root_ts = thread_ts.strip()
    if notification_kind not in {"question", "outcome", "action"}:
        raise ValueError(f"unknown Slack notification kind: {notification_kind!r}")
    if not clean_message or (notification_kind in {"question", "action"} and not clean_label):
        raise ValueError("a pending Slack notification cannot be empty")
    if clean_headline and root_ts:
        raise ValueError("a new headline cannot replace an existing thread root")
    if notification_kind in {"outcome", "action"} and clean_headline:
        raise ValueError("a run outcome cannot create a separate headline")

    existing = connection.execute(
        f"SELECT {_QUESTION_COLUMNS} FROM run_questions WHERE run_id = ? "
        "AND confirmed_at = '' AND satisfied_at = ''",
        (run_id,),
    ).fetchone()
    if existing is not None:
        pending = _from_row(existing)
        expected = (
            notification_kind,
            pending_status,
            target_status,
            clean_message,
            clean_label,
            clean_headline,
            root_ts,
        )
        actual = (
            pending.notification_kind,
            pending.pending_status,
            pending.target_status,
            pending.message,
            pending.question_label,
            pending.headline,
            pending.thread_ts,
        )
        if actual != expected:
            raise ValueError("this run already has a different Slack notification pending")
        return pending

    question_id = f"question-{uuid.uuid4().hex}"
    headline_client_id = str(uuid.uuid4())
    question_client_id = str(uuid.uuid4())
    now = _now()
    reason = confirmed_reason[:400]
    confirmation = confirmation_detail[:400]
    with _transition(connection):
        previous = connection.execute(
            "SELECT status, failure_reason FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if previous is None:
            raise ValueError(f"run cannot accept a Slack notification: {run_id}")
        connection.execute(
            """
            INSERT INTO run_questions (
                question_id, run_id, notification_kind, pending_status,
                target_status, message, question_label, headline, thread_ts,
                headline_client_id, question_client_id, confirmed_reason,
                confirmation_detail, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                question_id,
                run_id,
                notification_kind,
                pending_status,
                target_status,
                clean_message,
                clean_label,
                clean_headline,
                root_ts,
                headline_client_id,
                question_client_id,
                reason,
                confirmation,
                now,
            ),
        )
        if notification_kind == "action":
            if str(previous["status"]) != pending_status:
                raise ValueError(f"run changed while recording its action outcome: {run_id}")
        else:
            changed = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ?, failure_reason = ?, "
                "slack_thread_ts = CASE WHEN ? != '' THEN ? ELSE slack_thread_ts END, "
                "output_file_id = CASE WHEN ? != '' THEN ? ELSE output_file_id END, "
                "output_url = CASE WHEN ? != '' THEN ? ELSE output_url END "
                "WHERE run_id = ? AND status != 'delivered'",
                (
                    pending_status,
                    now,
                    SLACK_NOTIFICATION_PENDING,
                    root_ts,
                    root_ts,
                    output_file_id,
                    output_file_id,
                    output_url,
                    output_url,
                    run_id,
                ),
            )
            if changed.rowcount != 1:
                raise ValueError(f"run cannot accept a Slack notification: {run_id}")
        # recover_interrupted_runs already appended the truthful failed/review
        # transition. Converting its legacy durable marker into this stable-id
        # outbox is not a second state change and must not duplicate that event.
        if notification_kind != "action" and not (
            str(previous["status"]) == pending_status
            and str(previous["failure_reason"]) == INTERRUPTED_NOTIFICATION_PENDING
        ):
            connection.execute(
                "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
                (run_id, now, pending_status, transition_detail[:400]),
            )
    row = connection.execute(
        f"SELECT {_QUESTION_COLUMNS} FROM run_questions WHERE question_id = ?",
        (question_id,),
    ).fetchone()
    if row is None:  # pragma: no cover - guarded by the transaction above
        raise sqlite3.IntegrityError("the pending Slack notification was not stored")
    return _from_row(row)


def adopt_run_thread_for_notification(
    connection: sqlite3.Connection,
    question_id: str,
    thread_ts: str,
) -> bool:
    """Record that a thread-less notification belongs under its run's own thread.

    Narrower than `bind_run_question_thread` on purpose, and the difference is
    the whole point. Binding also writes `headline_ts`, which the delivery path
    reads as "this notification has already been posted, and that timestamp is
    its confirmation" — using it here would mark the outcome delivered without
    ever saying it. This writes the thread and nothing else, so the message is
    still owed and still gets posted, as a reply where it belongs.

    Args:
        connection: The open database.
        question_id: The notification adopting its run's thread.
        thread_ts: That run's existing Slack thread.

    Returns:
        True when the row now carries the thread. False when the notification
        is no longer actionable, or already answers to a different thread —
        neither is this function's to overrule.

    Raises:
        ValueError: if `thread_ts` is blank, which would erase the association
            rather than record one.
    """
    root_ts = thread_ts.strip()
    if not root_ts:
        raise ValueError("a notification cannot adopt an empty thread")
    with _transition(connection):
        row = connection.execute(
            "SELECT thread_ts FROM run_questions "
            "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = ''",
            (question_id,),
        ).fetchone()
        if row is None:
            return False
        existing = str(row["thread_ts"] or "").strip()
        if existing:
            return existing == root_ts
        connection.execute(
            "UPDATE run_questions SET thread_ts = ? "
            "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = '' AND thread_ts = ''",
            (root_ts, question_id),
        )
    return True


def bind_run_question_thread(
    connection: sqlite3.Connection,
    question_id: str,
    thread_ts: str,
) -> bool:
    """Associate a confirmed new headline with its run before the question."""
    root_ts = thread_ts.strip()
    if not root_ts:
        raise ValueError("Slack did not confirm the listing announcement")
    now = _now()
    with _transition(connection):
        row = connection.execute(
            "SELECT run_id, notification_kind, pending_status, headline, thread_ts, headline_ts "
            "FROM run_questions "
            "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = ''",
            (question_id,),
        ).fetchone()
        if row is None:
            return False
        existing_root = str(row["thread_ts"] or "").strip()
        if existing_root and existing_root != root_ts:
            return False
        existing_headline = str(row["headline_ts"] or "").strip()
        if existing_headline:
            return existing_headline == root_ts
        run_id = str(row["run_id"])
        pending_status = str(row["pending_status"])
        changed = connection.execute(
            "UPDATE runs SET updated_at = ?, slack_thread_ts = ? "
            "WHERE run_id = ? AND status = ? "
            "AND failure_reason = ? AND (slack_thread_ts = '' OR slack_thread_ts = ?)",
            (now, root_ts, run_id, pending_status, SLACK_NOTIFICATION_PENDING, root_ts),
        )
        if changed.rowcount != 1:
            return False
        connection.execute(
            "UPDATE run_questions SET thread_ts = ?, headline_ts = ?, "
            "delivery_claim_token = '', delivery_claimed_at = '' "
            "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = ''",
            (root_ts, root_ts, question_id),
        )
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (
                run_id,
                now,
                pending_status,
                (
                    "Slack confirmed the listing announcement"
                    if str(row["headline"] or "").strip()
                    else "Slack confirmed the notification thread root"
                ),
            ),
        )
    return True


def claim_run_notification_delivery(
    connection: sqlite3.Connection,
    question_id: str,
    phase: str,
    expected_attempt_count: int,
    stale_before: str,
) -> str:
    """Durably reserve one external Slack write before making it.

    The attempt marker and cross-process claim are the same atomic update. If
    the process dies before Slack answers, later workers reconcile but never
    blindly write again; an honest pending row is safer than a duplicate.
    """
    if phase not in {"headline", "question"}:
        raise ValueError("unknown run notification delivery phase")
    attempted_at = f"{phase}_attempted_at"
    attempt_count = f"{phase}_attempt_count"
    token = str(uuid.uuid4())
    now = _now()
    changed = connection.execute(
        f"""
        UPDATE run_questions
           SET delivery_claim_token = ?, delivery_claimed_at = ?,
               {attempted_at} = ?, {attempt_count} = {attempt_count} + 1
         WHERE question_id = ?
           AND confirmed_at = '' AND satisfied_at = ''
           AND {attempt_count} = ?
           AND (
               delivery_claim_token = ''
               OR delivery_claimed_at <= ?
           )
        """,
        (token, now, now, question_id, expected_attempt_count, stale_before),
    )
    return token if changed.rowcount == 1 else ""


def release_run_notification_delivery(
    connection: sqlite3.Connection,
    question_id: str,
    claim_token: str,
) -> None:
    """Release only this worker's live claim while retaining its attempt marker."""
    connection.execute(
        "UPDATE run_questions SET delivery_claim_token = '', delivery_claimed_at = '' "
        "WHERE question_id = ? AND delivery_claim_token = ?",
        (question_id, claim_token),
    )


def confirm_run_question(
    connection: sqlite3.Connection,
    question_id: str,
    notification_ts: str,
) -> bool:
    """Advance to the persisted target only after Slack confirms the message."""
    confirmed_ts = notification_ts.strip()
    if not confirmed_ts:
        raise ValueError("Slack did not confirm the question")
    now = _now()
    with _transition(connection):
        row = connection.execute(
            "SELECT run_id, notification_kind, pending_status, target_status, message, "
            "thread_ts, confirmed_reason, confirmation_detail FROM run_questions "
            "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = ''",
            (question_id,),
        ).fetchone()
        if row is None:
            return False
        target_status = str(row["target_status"])
        pending_status = str(row["pending_status"])
        if (
            target_status not in VALID_STATUSES
            or pending_status not in VALID_STATUSES
            or not str(row["thread_ts"] or "").strip()
        ):
            return False
        run_id = str(row["run_id"])
        if str(row["notification_kind"]) == "action":
            live = connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if live is None:
                return False
        else:
            changed = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ?, failure_reason = ? "
                "WHERE run_id = ? AND status = ? AND failure_reason = ?",
                (
                    target_status,
                    now,
                    str(row["confirmed_reason"])[:400],
                    run_id,
                    pending_status,
                    SLACK_NOTIFICATION_PENDING,
                ),
            )
            if changed.rowcount != 1:
                return False
        connection.execute(
            "UPDATE run_questions SET question_ts = ?, confirmed_at = ? "
            "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = ''",
            (confirmed_ts, now, question_id),
        )
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (
                run_id,
                now,
                target_status,
                (
                    f"{row['confirmation_detail']!s} at {confirmed_ts}"
                    if str(row["notification_kind"]) == "question"
                    else str(row["confirmation_detail"])
                )[:400],
            ),
        )
    return True


def pending_run_questions(connection: sqlite3.Connection) -> tuple[PendingRunQuestion, ...]:
    """Return every question whose Slack confirmation is still owed."""
    rows = connection.execute(
        f"SELECT {_QUESTION_COLUMNS} FROM run_questions "
        "WHERE confirmed_at = '' AND satisfied_at = '' ORDER BY created_at, rowid"
    ).fetchall()
    return tuple(_from_row(row) for row in rows)


def pending_run_question(
    connection: sqlite3.Connection,
    question_id: str,
) -> PendingRunQuestion | None:
    """Return one still-actionable outbox row, never a stale resolved copy."""
    row = connection.execute(
        f"SELECT {_QUESTION_COLUMNS} FROM run_questions "
        "WHERE question_id = ? AND confirmed_at = '' AND satisfied_at = ''",
        (question_id,),
    ).fetchone()
    return _from_row(row) if row is not None else None


def has_pending_run_notification(connection: sqlite3.Connection, run_id: str) -> bool:
    """Whether this run still owes any exact Slack message."""
    row = connection.execute(
        "SELECT 1 FROM run_questions WHERE run_id = ? "
        "AND confirmed_at = '' AND satisfied_at = '' LIMIT 1",
        (run_id,),
    ).fetchone()
    return row is not None


def has_run_action_notification(
    connection: sqlite3.Connection,
    run_id: str,
    action_id: str,
) -> bool:
    """Whether this exact Slack event already reached the mutation boundary."""
    row = connection.execute(
        "SELECT 1 FROM run_questions WHERE run_id = ? "
        "AND notification_kind = 'action' AND question_label = ? LIMIT 1",
        (run_id, action_id.strip()),
    ).fetchone()
    return row is not None
