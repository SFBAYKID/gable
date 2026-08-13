"""Interrupted flyer work always receives a durable, human-visible outcome."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.slackapp.runtime import notify_interrupted_runs
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
    still_pending = store.recover_interrupted_runs(connection)
    assert [notice.run_id for notice in still_pending] == [run.run_id]
    events_before_retry = connection.execute(
        "SELECT status FROM run_events WHERE run_id = ? ORDER BY id", (run.run_id,)
    ).fetchall()
    assert [event["status"] for event in events_before_retry] == ["pending", "failed"]

    assert (
        notify_interrupted_runs(
            connection,
            still_pending,
            lambda _message, _thread: "confirmed-retry",
        )
        == 1
    )
    assert store.recover_interrupted_runs(connection) == ()
