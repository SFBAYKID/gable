"""Interrupted flyer work always receives a durable, human-visible outcome."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.questions import (
    ReconcileState,
    Reconciliation,
    RunNotificationRetryLoop,
    RunQuestionRetryLoop,
)
from gable.slackapp.recovery import (
    enqueue_interrupted_runs,
    notify_interrupted_runs,
    notify_pending_run_questions,
)
from gable.slackapp.style import violations
from tests.runner_support import record, submission


def _db(tmp_path: Path) -> sqlite3.Connection:
    """Return a migrated recovery-test database."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    return connection


def test_startup_reports_threaded_and_unthreaded_interruptions_once(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    threaded_submission = submission(
        rid="rid-recovery-threaded",
        ts="8/13/2026 08:00:00",
        address="12 Threaded Way, Baltimore, MD 21201",
    )
    unthreaded_submission = submission(
        rid="rid-recovery-unthreaded",
        ts="8/13/2026 08:01:00",
        address="34 Silent Ave, Baltimore, MD 21201",
    )
    record(connection, threaded_submission)
    record(connection, unthreaded_submission)
    threaded = store.start_run(connection, threaded_submission.response_row_id)
    store.set_status(
        connection,
        threaded.run_id,
        "building",
        "photo placed",
        slack_thread_ts="thread-root",
    )
    unthreaded = store.start_run(connection, unthreaded_submission.response_row_id)

    notices = store.recover_interrupted_runs(connection)
    posts: list[tuple[str, str | None]] = []

    def say(message: str, thread_ts: str | None) -> str:
        posts.append((message, thread_ts))
        return f"notice-{len(posts)}"

    assert notify_interrupted_runs(connection, notices, say) == 2

    assert [thread for _message, thread in posts] == ["thread-root", None]
    assert "paused it for review" in posts[0][0]
    assert "marked that attempt failed" in posts[1][0]
    assert all(not violations(message) for message, _thread in posts)
    threaded_after = store.run_by_id(connection, threaded.run_id)
    unthreaded_after = store.run_by_id(connection, unthreaded.run_id)
    assert threaded_after is not None and threaded_after.status == "needs_review"
    assert threaded_after.slack_thread_ts == "thread-root"
    assert unthreaded_after is not None and unthreaded_after.status == "failed"
    assert unthreaded_after.slack_thread_ts == "notice-2"
    assert store.recover_interrupted_runs(connection) == ()


def test_unconfirmed_recovery_notice_is_retried_without_recovering_twice(
    tmp_path: Path,
) -> None:
    connection = _db(tmp_path)
    item = submission(rid="rid-recovery-retry", ts="8/13/2026 08:02:00")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    notices = store.recover_interrupted_runs(connection)

    assert notify_interrupted_runs(connection, notices, lambda _message, _thread: "") == 0
    assert store.recover_interrupted_runs(connection) == ()
    still_pending = store.pending_run_questions(connection)
    assert [notice.run_id for notice in still_pending] == [run.run_id]
    events_before_retry = connection.execute(
        "SELECT status FROM run_events WHERE run_id = ? ORDER BY id", (run.run_id,)
    ).fetchall()
    assert [event["status"] for event in events_before_retry] == ["pending", "failed"]

    assert (
        notify_pending_run_questions(
            connection,
            still_pending,
            lambda _message, _thread: "unused",
            reconcile=lambda *_args: Reconciliation(
                ReconcileState.FOUND,
                "confirmed-retry",
            ),
        )
        == 1
    )
    assert store.recover_interrupted_runs(connection) == ()


def test_a_pending_question_retries_once_with_the_same_slack_identity(
    tmp_path: Path,
) -> None:
    """A transient acknowledgement loss does not wait for another restart."""
    connection = _db(tmp_path)
    item = submission(rid="rid-question-retry", ts="8/13/2026 08:03:00")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    pending = store.prepare_run_question(
        connection,
        run.run_id,
        "needs_photo",
        "Can you send me the image?",
        thread_ts="thread-root",
    )
    calls: list[tuple[str, str | None, str]] = []

    def post_once(text: str, thread_ts: str | None, client_id: str) -> str:
        calls.append((text, thread_ts, client_id))
        return "" if len(calls) == 1 else "question-confirmed"

    assert (
        notify_pending_run_questions(
            connection,
            (pending,),
            lambda _text, _thread: "unused",
            post_once,
        )
        == 0
    )
    assert len(calls) == 1
    connection.execute(
        "UPDATE run_questions SET question_attempted_at = ? WHERE question_id = ?",
        ((datetime.now(UTC) - timedelta(minutes=3)).isoformat(), pending.question_id),
    )
    assert (
        notify_pending_run_questions(
            connection,
            store.pending_run_questions(connection),
            lambda _text, _thread: "unused",
            post_once,
            lambda *_args: Reconciliation(ReconcileState.ABSENT),
        )
        == 1
    )

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][2] == pending.question_client_id
    current = store.run_by_id(connection, run.run_id)
    assert current is not None and current.status == "needs_photo"
    assert store.pending_run_questions(connection) == ()
    transitions = connection.execute(
        "SELECT status FROM run_events WHERE run_id = ? ORDER BY id",
        (run.run_id,),
    ).fetchall()
    assert [str(row["status"]) for row in transitions].count("needs_photo") == 1


def test_a_pending_question_recovers_while_sheet_polling_is_disabled(
    tmp_path: Path,
) -> None:
    """Two failed posts remain durable and the running service tries again."""
    connection = _db(tmp_path)
    item = submission(rid="rid-question-next-poll", ts="8/13/2026 08:04:00")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    pending = store.prepare_run_question(
        connection,
        run.run_id,
        "needs_photo",
        "Can you send me the image?",
        thread_ts="thread-root",
    )
    first_calls: list[str] = []

    def unavailable(_text: str, _thread: str | None, client_id: str) -> str:
        first_calls.append(client_id)
        return ""

    assert (
        notify_pending_run_questions(
            connection,
            (pending,),
            lambda _text, _thread: "unused",
            unavailable,
        )
        == 0
    )
    assert first_calls == [pending.question_client_id]
    waiting = store.run_by_id(connection, run.run_id)
    assert waiting is not None and waiting.status == "needs_review"

    recovered_calls: list[str] = []

    recovered_event = threading.Event()

    connection.execute(
        "UPDATE run_questions SET question_attempted_at = ? WHERE question_id = ?",
        ((datetime.now(UTC) - timedelta(minutes=3)).isoformat(), pending.question_id),
    )

    def confirm_without_polling(_text: str, _thread: str | None, client_id: str) -> str:
        recovered_calls.append(client_id)
        recovered_event.set()
        return "question-without-sheet-polling"

    retry = RunQuestionRetryLoop(
        tmp_path / "gable.db",
        lambda _text, _thread: "unused",
        confirm_without_polling,
        reconcile=lambda *_args: Reconciliation(ReconcileState.ABSENT),
        interval_seconds=0.01,
    )
    retry.start()
    assert recovered_event.wait(timeout=2)
    retry.close()
    assert recovered_calls == [pending.question_client_id]
    assert store.pending_run_questions(connection) == ()
    recovered = store.run_by_id(connection, run.run_id)
    assert recovered is not None and recovered.status == "needs_photo"

    assert retry.drain_once() == 0
    assert recovered_calls == [pending.question_client_id]
    transitions = connection.execute(
        "SELECT status FROM run_events WHERE run_id = ? ORDER BY id",
        (run.run_id,),
    ).fetchall()
    assert [str(row["status"]) for row in transitions].count("needs_photo") == 1


def test_a_pending_final_outcome_is_not_reclassified_as_interrupted(tmp_path: Path) -> None:
    """A verified flyer awaiting Slack survives process restart as building."""
    connection = _db(tmp_path)
    item = submission(rid="rid-ready-restart", ts="8/13/2026 08:05:00")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    store.prepare_run_outcome(
        connection,
        run.run_id,
        "delivered",
        "Your flyer is ready. <https://slides.test/restart|Open the flyer>",
        pending_status="building",
        output_file_id="deck-restart",
        output_url="https://slides.test/restart",
    )

    assert store.recover_interrupted_runs(connection) == ()
    current = store.run_by_id(connection, run.run_id)
    assert current is not None and current.status == "building"
    assert current.output_file_id == "deck-restart"
    assert len(store.pending_run_questions(connection)) == 1


def test_interrupted_notice_enters_the_recurring_drain_without_restart(
    tmp_path: Path,
) -> None:
    """A failed startup post is retried by the same polling-independent loop."""
    connection = _db(tmp_path)
    item = submission(rid="rid-interrupted-loop", ts="8/13/2026 08:06:00")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    assert [item.run_id for item in store.recover_interrupted_runs(connection)] == [run.run_id]
    confirmed = threading.Event()
    calls: list[str] = []

    def post_once(_text: str, _thread: str | None, client_id: str) -> str:
        calls.append(client_id)
        confirmed.set()
        return "interrupted-confirmed"

    retry = RunNotificationRetryLoop(
        tmp_path / "gable.db",
        lambda _text, _thread: "unused",
        post_once,
        interval_seconds=0.01,
        prepare_pending=lambda retry_connection: enqueue_interrupted_runs(
            retry_connection,
            store.pending_interrupted_runs(retry_connection),
        ),
    )
    retry.start()
    assert confirmed.wait(timeout=2)
    retry.close()

    current = store.run_by_id(connection, run.run_id)
    assert current is not None and current.status == "failed"
    assert current.failure_reason == store.INTERRUPTED_REASON
    assert current.slack_thread_ts == "interrupted-confirmed"
    assert len(calls) == 1
    assert store.pending_run_questions(connection) == ()
