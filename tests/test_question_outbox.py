"""The two-message photo request is durable across either Slack failure boundary."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.questions import ReconcileState, Reconciliation, run_question_guard
from gable.slackapp.recovery import notify_pending_run_questions
from tests.runner_support import Recorder, record, runner, submission


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    return connection


def _unexpected_post(_text: str, _thread: str | None) -> str:
    pytest.fail("the idempotent post seam must be used")


def test_initial_question_never_posts_without_a_confirmed_listing_root(
    db: sqlite3.Connection,
) -> None:
    item = submission(rid="rid-root-unconfirmed")
    record(db, item)
    built = runner(db, Recorder())
    built.hero_photo_url = ""
    calls: list[tuple[str, str | None, str]] = []

    def reject_root(text: str, thread_ts: str | None, client_id: str) -> str:
        calls.append((text, thread_ts, client_id))
        return ""

    built.post_once = reject_root
    result = built.run(item)

    assert result.status == "needs_review"
    assert len(calls) == 1
    assert calls[0][1] is None
    assert "New Listing request" in calls[0][0]
    assert all("send me the image" not in text.lower() for text, _thread, _id in calls)
    current = store.run_by_id(db, result.run_id)
    assert current is not None
    assert current.status == "needs_review"
    assert current.slack_thread_ts == ""
    pending = store.pending_run_questions(db)
    assert len(pending) == 1
    assert pending[0].run_id == result.run_id
    assert pending[0].thread_ts == ""


def test_unconfirmed_initial_question_recovers_in_the_same_confirmed_thread(
    db: sqlite3.Connection,
) -> None:
    item = submission(rid="rid-question-unconfirmed")
    record(db, item)
    built = runner(db, Recorder())
    built.hero_photo_url = ""
    first_calls: list[tuple[str, str | None, str]] = []

    def lose_question_ack(text: str, thread_ts: str | None, client_id: str) -> str:
        first_calls.append((text, thread_ts, client_id))
        return "root-confirmed" if thread_ts is None else ""

    built.post_once = lose_question_ack
    result = built.run(item)

    assert result.status == "needs_review"
    assert len(first_calls) == 2
    assert first_calls[1][0] == "Can you send me the image?"
    assert first_calls[1][1] == "root-confirmed"
    pending = store.pending_run_questions(db)
    assert len(pending) == 1
    assert pending[0].thread_ts == "root-confirmed"
    current = store.run_by_id(db, result.run_id)
    assert current is not None
    assert current.status == "needs_review"
    assert current.slack_thread_ts == "root-confirmed"

    retried: list[tuple[str, str | None, str]] = []

    def confirm_question(text: str, thread_ts: str | None, client_id: str) -> str:
        retried.append((text, thread_ts, client_id))
        return "question-confirmed"

    def reconcile_question(
        _text: str,
        _thread: str | None,
        _created_at: str,
        _client_id: str,
    ) -> Reconciliation:
        return Reconciliation(ReconcileState.FOUND, "question-confirmed")

    assert (
        notify_pending_run_questions(
            db,
            pending,
            _unexpected_post,
            confirm_question,
            reconcile_question,
        )
        == 1
    )
    assert retried == []
    recovered = store.run_by_id(db, result.run_id)
    assert recovered is not None
    assert recovered.status == "needs_photo"
    assert recovered.run_id == result.run_id
    assert store.run_attempt_count(db, item.response_row_id) == 1
    assert store.pending_run_questions(db) == ()
    assert notify_pending_run_questions(db, (), _unexpected_post, confirm_question) == 0


def test_startup_delivers_a_question_persisted_before_any_slack_call(
    db: sqlite3.Connection,
) -> None:
    item = submission(rid="rid-crash-before-post")
    record(db, item)
    run = store.start_run(db, item.response_row_id)
    pending = store.prepare_run_question(
        db,
        run.run_id,
        "needs_photo",
        "Can you send me the image?",
        headline="New Sold request from Test Agent — 1 Main St",
    )
    before = store.run_by_id(db, run.run_id)
    assert before is not None
    assert before.status == "needs_review"
    assert before.slack_thread_ts == ""

    calls: list[tuple[str, str | None, str]] = []

    def post_once(text: str, thread_ts: str | None, client_id: str) -> str:
        calls.append((text, thread_ts, client_id))
        return "root-after-restart" if thread_ts is None else "question-after-restart"

    assert notify_pending_run_questions(db, (pending,), _unexpected_post, post_once) == 1
    assert calls == [
        (pending.headline, None, pending.headline_client_id),
        (pending.message, "root-after-restart", pending.question_client_id),
    ]
    after = store.run_by_id(db, run.run_id)
    assert after is not None
    assert after.status == "needs_photo"
    assert after.slack_thread_ts == "root-after-restart"
    assert store.pending_run_questions(db) == ()


def test_a_photo_claim_wins_before_a_stale_delivery_attempt(
    db: sqlite3.Connection,
) -> None:
    """A resolved stale snapshot cannot post its question after the upload."""
    item = submission(rid="rid-photo-wins", ts="8/13/2026 08:05:00")
    record(db, item)
    run = store.start_run(db, item.response_row_id)
    pending = store.prepare_run_question(
        db,
        run.run_id,
        "needs_photo",
        "Can you send the correct property image?",
        thread_ts="owned-root",
    )

    with run_question_guard(run.run_id):
        assert store.claim_run_for_photo(
            db,
            run.run_id,
            "owned-root",
            {"failure_reason": "", "photo_url": "http://images.test/new.jpg"},
        )

    posts: list[str] = []

    def stale_post(_text: str, _thread: str | None, client_id: str) -> str:
        posts.append(client_id)
        return "stale-post"

    assert (
        notify_pending_run_questions(
            db,
            (pending,),
            _unexpected_post,
            stale_post,
        )
        == 0
    )
    assert posts == []
    current = store.run_by_id(db, run.run_id)
    assert current is not None and current.status == "pending"
    assert current.photo_url == "http://images.test/new.jpg"


def test_delivery_and_photo_claim_are_serialized_exactly_once(
    db: sqlite3.Connection,
) -> None:
    """Whichever side owns the run guard first leaves one coherent outcome."""
    item = submission(rid="rid-delivery-wins", ts="8/13/2026 08:06:00")
    record(db, item)
    run = store.start_run(db, item.response_row_id)
    pending = store.prepare_run_question(
        db,
        run.run_id,
        "needs_photo",
        "Can you send me the image?",
        thread_ts="owned-root",
    )
    database_path = str(db.execute("PRAGMA database_list").fetchone()["file"])
    post_started = threading.Event()
    release_post = threading.Event()
    claim_finished = threading.Event()
    posts: list[str] = []

    def post_once(_text: str, _thread: str | None, client_id: str) -> str:
        posts.append(client_id)
        post_started.set()
        assert release_post.wait(timeout=2)
        return "confirmed-question"

    def deliver() -> int:
        delivery_connection = connect(database_path)
        try:
            return notify_pending_run_questions(
                delivery_connection,
                (pending,),
                _unexpected_post,
                post_once,
            )
        finally:
            delivery_connection.close()

    def claim_photo() -> bool:
        claim_connection = connect(database_path)
        try:
            with run_question_guard(run.run_id):
                claimed = store.claim_run_for_photo(
                    claim_connection,
                    run.run_id,
                    "owned-root",
                    {"failure_reason": "", "photo_url": "http://images.test/claimed.jpg"},
                )
            claim_finished.set()
            return claimed
        finally:
            claim_connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        delivering = pool.submit(deliver)
        assert post_started.wait(timeout=2)
        claiming = pool.submit(claim_photo)
        assert not claim_finished.wait(timeout=0.1)
        release_post.set()
        assert delivering.result(timeout=2) == 1
        assert claiming.result(timeout=2) is True

    assert posts == [pending.question_client_id]
    current = store.run_by_id(db, run.run_id)
    assert current is not None and current.status == "pending"
    assert current.photo_url == "http://images.test/claimed.jpg"
    row = db.execute(
        "SELECT confirmed_at, satisfied_at FROM run_questions WHERE question_id = ?",
        (pending.question_id,),
    ).fetchone()
    assert row is not None and row["confirmed_at"] and not row["satisfied_at"]


@pytest.mark.parametrize("kind", ["question", "outcome"])
def test_a_generic_resume_cannot_claim_an_unresolved_notification(
    db: sqlite3.Connection,
    kind: str,
) -> None:
    """Conversation retries wait until the exact pending Slack message resolves."""
    item = submission(rid=f"rid-claim-blocked-{kind}", ts="8/13/2026 08:07:00")
    record(db, item)
    run = store.start_run(db, item.response_row_id)
    if kind == "question":
        pending = store.prepare_run_question(
            db,
            run.run_id,
            "needs_info",
            "What direct phone should I use for this listing?",
            thread_ts="owned-root",
        )
        confirmed_status = "needs_info"
    else:
        pending = store.prepare_run_outcome(
            db,
            run.run_id,
            "needs_review",
            "I rendered it, but the final check was inconclusive.",
            thread_ts="owned-root",
            confirmed_reason="the final check was inconclusive",
        )
        confirmed_status = "needs_review"

    assert not store.claim_paused_run(db, run.run_id, expected_status="needs_review")
    assert store.confirm_run_question(db, pending.question_id, "confirmed-notice")
    assert store.claim_paused_run(db, run.run_id, expected_status=confirmed_status)
