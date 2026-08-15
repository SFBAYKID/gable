"""Durable review, failure, and final-link Slack outcome invariants."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.questions import (
    ReconcileState,
    Reconciliation,
    RunNotificationRetryLoop,
)
from gable.slackapp.recovery import notify_pending_run_questions
from tests.runner_support import record, submission


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Return one migrated outbox database."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    return connection


def _run(connection: sqlite3.Connection, rid: str) -> store.RunRow:
    """Open one persisted run with a distinct submission."""
    item = submission(rid=rid, ts=f"8/13/2026 09:{len(rid):02d}:00")
    record(connection, item)
    return store.start_run(connection, item.response_row_id)


def _unexpected_post(_text: str, _thread: str | None) -> str:
    pytest.fail("durable outcomes must use their stable Slack identity")


def test_a_root_delivery_outcome_uses_truthful_states_and_confirms_once(
    db: sqlite3.Connection,
) -> None:
    """A root link stays building, never review, until Slack returns its timestamp."""
    run = _run(db, "rid-root-outcome")
    pending = store.prepare_run_outcome(
        db,
        run.run_id,
        "delivered",
        "Your flyer is ready. <https://slides.test/one|Open the flyer>",
        pending_status="building",
        output_file_id="deck-one",
        output_url="https://slides.test/one",
        transition_detail="flyer verified and waiting for its Slack delivery message",
        confirmation_detail="Slack confirmed the delivery message",
    )
    calls: list[tuple[str | None, str]] = []

    def post_once(_text: str, thread_ts: str | None, client_id: str) -> str:
        calls.append((thread_ts, client_id))
        return "root-ready"

    assert notify_pending_run_questions(db, (pending,), _unexpected_post, post_once) == 1
    assert calls == [(None, pending.question_client_id)]
    current = store.run_by_id(db, run.run_id)
    assert current is not None
    assert current.status == "delivered"
    assert current.slack_thread_ts == "root-ready"
    assert current.output_file_id == "deck-one"
    assert store.pending_run_questions(db) == ()
    events = db.execute(
        "SELECT status, detail FROM run_events WHERE run_id = ? ORDER BY id",
        (run.run_id,),
    ).fetchall()
    assert all(str(row["status"]) != "needs_review" for row in events)
    assert [str(row["detail"]) for row in events if row["status"] == "delivered"] == [
        "Slack confirmed the delivery message"
    ]
    assert notify_pending_run_questions(db, (pending,), _unexpected_post, post_once) == 0
    assert calls == [(None, pending.question_client_id)]


def test_an_outcome_recorded_without_a_thread_is_posted_into_its_run_s_thread(
    db: sqlite3.Connection,
) -> None:
    """The Sold review that could never resolve, from 2026-08-14 to the next day.

    The run had already announced itself, so the outcome could not claim a new
    thread and the bind was refused on every pass — once a minute, for a day.
    It belongs under the announcement, which is also where a reader expects it.
    """
    run = _run(db, "rid-thread-less-outcome")
    db.execute(
        "UPDATE runs SET slack_thread_ts = ? WHERE run_id = ?",
        ("1786720900.051499", run.run_id),
    )
    db.commit()
    pending = store.prepare_run_outcome(
        db,
        run.run_id,
        "needs_review",
        "I rendered it, but the word “Today.” overlaps the footer bar.\nI have not sent it.",
    )
    assert pending.thread_ts == ""
    posted: list[str | None] = []

    def post_once(_text: str, thread_ts: str | None, _client_id: str) -> str:
        posted.append(thread_ts)
        return "1786720912.991259"

    assert notify_pending_run_questions(db, (pending,), _unexpected_post, post_once) == 1

    # Under the announcement, not as a second root message in the channel.
    assert posted == ["1786720900.051499"]
    current = store.run_by_id(db, run.run_id)
    assert current is not None
    assert current.status == "needs_review"
    assert current.slack_thread_ts == "1786720900.051499"
    assert store.pending_run_questions(db) == ()


def test_an_outcome_for_a_run_that_never_announced_still_becomes_the_root(
    db: sqlite3.Connection,
) -> None:
    """A local run with no Slack root must not be left unreachable."""
    run = _run(db, "rid-no-root-yet")
    pending = store.prepare_run_outcome(db, run.run_id, "needs_review", "I could not read it.")

    def post_once(_text: str, thread_ts: str | None, _client_id: str) -> str:
        assert thread_ts is None
        return "fresh-root"

    assert notify_pending_run_questions(db, (pending,), _unexpected_post, post_once) == 1
    current = store.run_by_id(db, run.run_id)
    assert current is not None
    assert current.slack_thread_ts == "fresh-root"


def test_an_accepted_link_with_a_lost_ack_recovers_in_the_running_process(
    tmp_path: Path,
) -> None:
    """Every retry carries one client id, yielding one visible link and one event."""
    path = tmp_path / "gable.db"
    connection = connect(path)
    apply_migrations(connection)
    run = _run(connection, "rid-ack-loss")
    pending = store.prepare_run_outcome(
        connection,
        run.run_id,
        "delivered",
        "Your flyer is ready. <https://slides.test/two|Open the flyer>",
        pending_status="building",
        output_file_id="deck-two",
        output_url="https://slides.test/two",
        confirmation_detail="Slack confirmed the delivery message",
    )
    visible: dict[str, str] = {}
    attempts: list[str] = []

    def lose_ack(_text: str, _thread: str | None, client_id: str) -> str:
        attempts.append(client_id)
        visible.setdefault(client_id, "visible-ready")
        return ""

    recovered = threading.Event()
    reconciliation_calls = 0

    def reconcile(
        _text: str,
        _thread: str | None,
        _created_at: str,
        _client_id: str,
    ) -> Reconciliation:
        nonlocal reconciliation_calls
        reconciliation_calls += 1
        if reconciliation_calls == 1:
            return Reconciliation(ReconcileState.ABSENT)
        if reconciliation_calls == 2:
            return Reconciliation(ReconcileState.UNKNOWN)
        recovered.set()
        return Reconciliation(ReconcileState.FOUND, "visible-ready")

    assert (
        notify_pending_run_questions(
            connection,
            (pending,),
            _unexpected_post,
            lose_ack,
            reconcile,
        )
        == 0
    )
    assert attempts == [pending.question_client_id]

    def no_second_write(_text: str, _thread: str | None, _client_id: str) -> str:
        pytest.fail("the next drain must reconcile before another Slack write")

    retry = RunNotificationRetryLoop(
        path,
        _unexpected_post,
        no_second_write,
        reconcile=reconcile,
        interval_seconds=0.01,
    )
    retry.start()
    assert recovered.wait(timeout=2)
    retry.close()

    assert set(attempts) == {pending.question_client_id}
    assert visible == {pending.question_client_id: "visible-ready"}
    current = store.run_by_id(connection, run.run_id)
    assert current is not None and current.status == "delivered"
    assert store.pending_run_questions(connection) == ()
    count = connection.execute(
        "SELECT COUNT(*) AS n FROM run_events WHERE run_id = ? AND status = 'delivered'",
        (run.run_id,),
    ).fetchone()
    assert count is not None and int(count["n"]) == 1
    assert retry.drain_once() == 0
    assert attempts == [pending.question_client_id]


def test_history_reconciliation_confirms_an_accepted_link_without_another_post(
    db: sqlite3.Connection,
) -> None:
    """A proved first post closes SQLite before a second Slack write."""
    run = _run(db, "rid-history-reconcile")
    pending = store.prepare_run_outcome(
        db,
        run.run_id,
        "delivered",
        "Your flyer is ready. <https://slides.test/history|Open the flyer>",
        pending_status="building",
        output_file_id="deck-history",
        output_url="https://slides.test/history",
    )
    attempts: list[str] = []
    reconciliations: list[tuple[str, str | None, str]] = []

    def lose_ack(_text: str, _thread: str | None, client_id: str) -> str:
        attempts.append(client_id)
        return ""

    def reconcile(
        text: str,
        thread_ts: str | None,
        created_at: str,
        _client_id: str,
    ) -> Reconciliation:
        reconciliations.append((text, thread_ts, created_at))
        return Reconciliation(ReconcileState.FOUND, "visible-ready")

    assert (
        notify_pending_run_questions(
            db,
            (pending,),
            _unexpected_post,
            lose_ack,
            reconcile,
        )
        == 1
    )
    assert attempts == []
    assert reconciliations == [(pending.message, None, pending.created_at)]
    current = store.run_by_id(db, run.run_id)
    assert current is not None and current.status == "delivered"
    assert current.slack_thread_ts == "visible-ready"
    assert store.pending_run_questions(db) == ()


def test_proved_absence_after_grace_allows_one_bounded_same_id_retry(
    db: sqlite3.Connection,
) -> None:
    """Unknown delivery never retries blindly; complete absence restores liveness."""
    run = _run(db, "rid-proved-absent-retry")
    pending = store.prepare_run_outcome(
        db,
        run.run_id,
        "needs_review",
        "I stopped because the flyer needs review.",
        thread_ts="owned-root",
    )
    writes: list[str] = []
    history_calls = 0

    def fail_once(_text: str, _thread: str | None, client_id: str) -> str:
        writes.append(client_id)
        raise ConnectionError("definite test outage")

    def first_history(*_args: object) -> Reconciliation:
        nonlocal history_calls
        history_calls += 1
        return Reconciliation(
            ReconcileState.ABSENT if history_calls == 1 else ReconcileState.UNKNOWN
        )

    assert (
        notify_pending_run_questions(
            db,
            (pending,),
            _unexpected_post,
            fail_once,
            first_history,
        )
        == 0
    )
    after_failure = store.pending_run_question(db, pending.question_id)
    assert after_failure is not None and after_failure.question_attempt_count == 1
    old = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    db.execute(
        "UPDATE run_questions SET question_attempted_at = ? WHERE question_id = ?",
        (old, pending.question_id),
    )

    def retry(_text: str, _thread: str | None, client_id: str) -> str:
        writes.append(client_id)
        return "review-confirmed"

    assert (
        notify_pending_run_questions(
            db,
            (pending,),
            _unexpected_post,
            retry,
            lambda *_args: Reconciliation(ReconcileState.ABSENT),
        )
        == 1
    )
    assert writes == [pending.question_client_id, pending.question_client_id]


def test_unknown_history_after_an_attempt_never_triggers_another_write(
    db: sqlite3.Connection,
) -> None:
    run = _run(db, "rid-unknown-no-retry")
    pending = store.prepare_run_outcome(
        db,
        run.run_id,
        "needs_review",
        "I stopped because the flyer needs review.",
        thread_ts="owned-root",
    )
    old = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    db.execute(
        "UPDATE run_questions SET question_attempted_at = ?, "
        "question_attempt_count = 1 WHERE question_id = ?",
        (old, pending.question_id),
    )

    def forbidden(_text: str, _thread: str | None, _client_id: str) -> str:
        pytest.fail("unavailable or ambiguous history cannot authorize a retry")

    assert (
        notify_pending_run_questions(
            db,
            (pending,),
            _unexpected_post,
            forbidden,
            lambda *_args: Reconciliation(ReconcileState.UNKNOWN),
        )
        == 0
    )


def test_two_database_connections_cannot_claim_the_same_external_write(
    tmp_path: Path,
) -> None:
    """The durable CAS, not only the process-local guard, elects one writer."""
    path = tmp_path / "gable.db"
    setup = connect(path)
    apply_migrations(setup)
    run = _run(setup, "rid-two-connections")
    pending = store.prepare_run_outcome(
        setup,
        run.run_id,
        "needs_review",
        "I stopped because the flyer needs review.",
        thread_ts="owned-root",
    )
    barrier = threading.Barrier(2)

    def claim() -> str:
        connection = connect(path)
        try:
            barrier.wait()
            return store.claim_run_notification_delivery(
                connection,
                pending.question_id,
                "question",
                0,
                (datetime.now(UTC) - timedelta(minutes=3)).isoformat(),
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(lambda _index: claim(), range(2)))

    assert sum(bool(token) for token in tokens) == 1
    current = store.pending_run_question(setup, pending.question_id)
    assert current is not None
    assert current.question_attempt_count == 1
    assert current.delivery_claim_token == next(token for token in tokens if token)
    setup.close()


def test_stale_crash_claim_recovers_only_after_timeout_and_proved_absence(
    db: sqlite3.Connection,
) -> None:
    """A crash before POST cannot strand the row or overlap a live HTTP call."""
    run = _run(db, "rid-stale-claim")
    pending = store.prepare_run_outcome(
        db,
        run.run_id,
        "needs_review",
        "I stopped because the flyer needs review.",
        thread_ts="owned-root",
    )
    old_boundary = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
    first = store.claim_run_notification_delivery(
        db,
        pending.question_id,
        "question",
        0,
        old_boundary,
    )
    assert first
    # The active lease is newer than the stale boundary, so another connection
    # cannot fence or replace its potentially in-flight Slack request.
    assert not store.claim_run_notification_delivery(
        db,
        pending.question_id,
        "question",
        1,
        old_boundary,
    )
    stale = (datetime.now(UTC) - timedelta(minutes=4)).isoformat()
    db.execute(
        "UPDATE run_questions SET delivery_claimed_at = ?, question_attempted_at = ? "
        "WHERE question_id = ?",
        (stale, stale, pending.question_id),
    )
    writes: list[str] = []

    def retry(_text: str, _thread: str | None, client_id: str) -> str:
        writes.append(client_id)
        return "stale-claim-confirmed"

    assert (
        notify_pending_run_questions(
            db,
            (pending,),
            _unexpected_post,
            retry,
            lambda *_args: Reconciliation(ReconcileState.ABSENT),
        )
        == 1
    )
    assert writes == [pending.question_client_id]


def test_more_than_three_definite_delivery_failures_still_recover(
    db: sqlite3.Connection,
) -> None:
    """The listing build cap never turns a durable Slack notice into data loss."""
    run = _run(db, "rid-many-slack-failures")
    pending = store.prepare_run_outcome(
        db,
        run.run_id,
        "delivered",
        "Your flyer is ready. <https://slides.test/many|Open the flyer>",
        pending_status="building",
        output_file_id="deck-many",
        output_url="https://slides.test/many",
    )
    writes = 0

    def flaky(_text: str, _thread: str | None, _client_id: str) -> str:
        nonlocal writes
        writes += 1
        if writes <= 4:
            raise ConnectionError("definite test outage")
        return "many-failures-confirmed"

    def absent(*_args: object) -> Reconciliation:
        return Reconciliation(ReconcileState.ABSENT)

    for expected_attempt in range(1, 6):
        assert notify_pending_run_questions(
            db,
            (pending,),
            _unexpected_post,
            flaky,
            absent,
        ) == (1 if expected_attempt == 5 else 0)
        if expected_attempt < 5:
            db.execute(
                "UPDATE run_questions SET question_attempted_at = ? WHERE question_id = ?",
                (
                    (datetime.now(UTC) - timedelta(minutes=3)).isoformat(),
                    pending.question_id,
                ),
            )
    assert writes == 5
    current = store.run_by_id(db, run.run_id)
    assert current is not None and current.status == "delivered"


@pytest.mark.parametrize(
    ("target_status", "thread_ts", "failure_mode"),
    [
        ("needs_review", "owned-root", "blank"),
        ("needs_review", "owned-root", "raise"),
        ("failed", "", "blank"),
        ("failed", "", "raise"),
    ],
)
def test_review_and_failure_outcomes_survive_blank_or_exceptional_slack(
    db: sqlite3.Connection,
    target_status: str,
    thread_ts: str,
    failure_mode: str,
) -> None:
    """The exact actionable outcome remains pending until a later confirmation."""
    run = _run(db, f"rid-{target_status}-{failure_mode}")
    detail = f"actionable {target_status} detail"
    pending = store.prepare_run_outcome(
        db,
        run.run_id,
        target_status,
        f"I stopped with a {target_status} outcome.",
        thread_ts=thread_ts,
        confirmed_reason=detail,
        confirmation_detail=f"Slack confirmed {target_status}",
        transition_detail=detail,
    )
    attempts: list[str] = []

    def unavailable(_text: str, _thread: str | None, client_id: str) -> str:
        attempts.append(client_id)
        if failure_mode == "raise":
            raise RuntimeError("temporary Slack outage")
        return ""

    assert notify_pending_run_questions(db, (pending,), _unexpected_post, unavailable) == 0
    waiting = store.run_by_id(db, run.run_id)
    assert waiting is not None and waiting.status == target_status
    assert waiting.failure_reason == store.QUESTION_NOTIFICATION_PENDING
    assert attempts == [pending.question_client_id]

    def recovered(_text: str, _thread: str | None, _client_id: str) -> str:
        pytest.fail("a prior uncertain write must be reconciled before retrying")

    def accepted(
        _text: str,
        _thread: str | None,
        _created_at: str,
        _client_id: str,
    ) -> Reconciliation:
        return Reconciliation(ReconcileState.FOUND, "confirmed-outcome")

    assert (
        notify_pending_run_questions(
            db,
            (pending,),
            _unexpected_post,
            recovered,
            accepted,
        )
        == 1
    )
    assert set(attempts) == {pending.question_client_id}
    current = store.run_by_id(db, run.run_id)
    assert current is not None and current.status == target_status
    assert current.failure_reason == detail
    assert current.slack_thread_ts == (thread_ts or "confirmed-outcome")
    assert store.pending_run_questions(db) == ()


def test_two_concurrent_outcome_drains_post_and_confirm_once(tmp_path: Path) -> None:
    """A stale snapshot cannot send or confirm a second review outcome."""
    path = tmp_path / "gable.db"
    setup = connect(path)
    apply_migrations(setup)
    run = _run(setup, "rid-concurrent-outcome")
    pending = store.prepare_run_outcome(
        setup,
        run.run_id,
        "needs_review",
        "I rendered it, but the final check was inconclusive.",
        thread_ts="owned-root",
        confirmed_reason="the final check was inconclusive",
    )
    post_started = threading.Event()
    release_post = threading.Event()
    posts: list[str] = []

    def post_once(_text: str, _thread: str | None, client_id: str) -> str:
        posts.append(client_id)
        post_started.set()
        assert release_post.wait(timeout=5)
        return "review-outcome"

    def drain() -> int:
        connection = connect(path)
        try:
            return notify_pending_run_questions(
                connection,
                (pending,),
                _unexpected_post,
                post_once,
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(drain)
        assert post_started.wait(timeout=5)
        second = pool.submit(drain)
        release_post.set()
        assert sorted((first.result(timeout=5), second.result(timeout=5))) == [0, 1]

    assert posts == [pending.question_client_id]
    current = store.run_by_id(setup, run.run_id)
    assert current is not None and current.status == "needs_review"
    confirmations = setup.execute(
        "SELECT COUNT(*) AS n FROM run_events WHERE run_id = ? "
        "AND detail = 'Slack confirmed the run outcome'",
        (run.run_id,),
    ).fetchone()
    assert confirmations is not None and int(confirmations["n"]) == 1
