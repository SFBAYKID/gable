"""A run never says the same sentence to the same thread twice.

Lina Mariner's thread on 2026-09-01: the same address question three times,
each after Carmen had answered it. Whatever the cause underneath, the repeat
is what a person sees as "not listening".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline import run_speech
from gable.pipeline.run_reporting import RunResult
from gable.slackapp import resume
from gable.voice import is_clean
from tests.runner_support import record, submission

QUESTION = (
    "The address reads '10600 Partridge Ln Apt B3, Cockeysville, MD 21030', which looks "
    "like more than one property. Which one is this post for?"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """A migrated database."""
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


def test_a_new_question_passes_through() -> None:
    assert run_speech.repeat_guard((), QUESTION) == QUESTION
    assert run_speech.repeat_guard(("Can you send me the image?",), QUESTION) == QUESTION


def test_the_second_time_is_an_escalation_that_still_says_what_is_wrong() -> None:
    escalated = run_speech.repeat_guard((QUESTION,), QUESTION)

    assert escalated is not None
    assert escalated != QUESTION
    assert QUESTION in escalated
    assert "will not ask again" in escalated
    assert is_clean(escalated)


def test_the_third_time_is_silence() -> None:
    escalated = run_speech.repeat_guard((QUESTION,), QUESTION)
    assert escalated is not None

    assert run_speech.repeat_guard((QUESTION, escalated), QUESTION) is None
    # Case and spacing do not make a question new.
    assert (
        run_speech.repeat_guard((QUESTION.upper(), escalated), "  ".join(QUESTION.split())) is None
    )


def test_a_different_question_after_an_escalation_is_asked_normally() -> None:
    escalated = run_speech.repeat_guard((QUESTION,), QUESTION)
    assert escalated is not None

    other = "Can you send me the image?"
    assert run_speech.repeat_guard((QUESTION, escalated), other) == other


def test_a_thread_hears_the_question_once_the_escalation_once_and_then_nothing(
    db: sqlite3.Connection,
) -> None:
    """The whole path: three identical asks, two messages, run still paused."""
    item = submission(rid="rid-repeat")
    record(db, item)
    run = store.start_run(db, item.response_row_id)
    said: list[str] = []

    def say(text: str, _thread: str | None) -> str:
        said.append(text)
        return f"1788.{len(said)}"

    for attempt in range(3):
        if attempt:
            # A resume reopens the run before the next ask, as the real path does.
            store.set_status(db, run.run_id, "pending", "resumed for the test")
        result = run_speech.deliver_question(
            db,
            say,
            run.run_id,
            item.intake,
            QUESTION,
            [],
            RunResult(run_id=run.run_id, status="pending"),
            status="needs_info",
            thread_ts="1788.0",
        )
        assert result.status == "needs_info"

    gable_messages = [text for text in said if text]
    assert len(gable_messages) == 2, said
    assert gable_messages[0] == QUESTION
    assert QUESTION in gable_messages[1]
    assert "will not ask again" in gable_messages[1]
    current = store.run_by_id(db, run.run_id)
    assert current is not None
    assert current.status == "needs_info"
    assert current.failure_reason == QUESTION


def test_the_silent_third_ask_is_reported_as_a_decision_not_a_blank(
    db: sqlite3.Connection,
) -> None:
    """The caller has to tell withheld silence from a run that fell over.

    Ian DePinto's 2026-09-21 thread ended on two identical "I picked this
    listing back up, but the run did not produce an outcome I could report"
    replies -- once after Carmen answered in the thread, once after she put the
    value in the sheet and asked for a rerun. That sentence is what this
    conflation sounds like to whoever is waiting.
    """
    item = submission(rid="rid-silent")
    record(db, item)
    run = store.start_run(db, item.response_row_id)

    def say(_text: str, _thread: str | None) -> str:
        return "1788.0"

    results = []
    for attempt in range(3):
        if attempt:
            store.set_status(db, run.run_id, "pending", "resumed for the test")
        results.append(
            run_speech.deliver_question(
                db,
                say,
                run.run_id,
                item.intake,
                QUESTION,
                [],
                RunResult(run_id=run.run_id, status="pending"),
                status="needs_info",
                thread_ts="1788.0",
            )
        )

    assert [item.already_escalated for item in results] == [False, False, True]


def test_a_rerun_of_an_escalated_listing_says_what_it_checked() -> None:
    """Named, so a person can tell a re-read that failed from a crash."""
    escalated = resume.silent_outcome_words(
        RunResult(run_id="r", status="needs_info", already_escalated=True)
    )

    assert escalated == resume.ALREADY_ESCALATED
    assert "read this listing's form row" in escalated
    assert "still paused" in escalated
    assert "flagged this one for Chase" in escalated
    # It must not carry the question a third time; that is what was withheld.
    assert QUESTION not in escalated
    assert is_clean(escalated)


def test_an_ordinary_silent_pause_still_reports_itself_as_one() -> None:
    """Only the escalated case changes; the others are untouched."""
    paused = resume.silent_outcome_words(RunResult(run_id="r", status="needs_info"))
    finished = resume.silent_outcome_words(RunResult(run_id="r", status="failed"))

    assert "left the listing paused" in paused
    assert "left the current flyer unchanged" in finished
    assert is_clean(paused)
    assert is_clean(finished)
