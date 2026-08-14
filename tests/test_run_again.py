"""Building the same flyer again, after it was already delivered.

A delivered run is terminal, so every "run it again" used to answer "this
listing is already being rechecked or is no longer waiting" — the one thing
Carmen is most likely to ask for after reading a flyer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.slackapp.intents import asks_to_run_again
from tests.runner_support import record, submission

THREAD = "1786468156.701419"


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


def _delivered_run(connection: sqlite3.Connection, status: str = "delivered") -> str:
    """Create one finished run that owns its Slack thread."""
    store.record_submission(
        connection,
        "response-again",
        7,
        "response-again",
        Intake(
            agent_email="andy@cornerhouserealty.com",
            agent_name="Andy Jang",
            request_type="Under Contract",
            address="3283 Doyle Place, Aberdeen, MD 21009",
            post_details="",
            open_house="",
            new_price="",
            closing_price="",
            extra_notes="",
            side="",
            notes="",
        ),
    )
    run_id = store.start_run(connection, "response-again").run_id
    store.set_status(connection, run_id, "building", "building", slack_thread_ts=THREAD)
    store.set_status(connection, run_id, status, "finished")
    return run_id


def test_a_delivered_run_can_be_reopened_and_then_claimed(db: sqlite3.Connection) -> None:
    """The whole point: the next step is a claim, and it has to succeed."""
    run_id = _delivered_run(db)

    assert store.reopen_for_rebuild(db, run_id, "action-1", THREAD) is True
    assert store.claim_paused_run(db, run_id) is True


def test_the_same_request_delivered_twice_rebuilds_once(db: sqlite3.Connection) -> None:
    """Slack redelivers; a flyer must not be rebuilt for one request twice."""
    run_id = _delivered_run(db)

    first = store.reopen_for_rebuild(db, run_id, "action-1", THREAD)
    second = store.reopen_for_rebuild(db, run_id, "action-1", THREAD)

    assert (first, second) == (True, False)


def test_a_request_from_another_thread_is_refused(db: sqlite3.Connection) -> None:
    run_id = _delivered_run(db)

    assert store.reopen_for_rebuild(db, run_id, "action-1", "9999999999.000000") is False


def test_a_run_still_in_flight_is_not_reopened(db: sqlite3.Connection) -> None:
    """Only a finished run is reopened; an active one is already working."""
    run_id = _delivered_run(db, status="delivered")
    store.set_status(db, run_id, "building", "already working")

    assert store.reopen_for_rebuild(db, run_id, "action-1", THREAD) is False


def test_reopening_is_recorded_as_an_event(db: sqlite3.Connection) -> None:
    run_id = _delivered_run(db)

    store.reopen_for_rebuild(db, run_id, "action-1", THREAD)

    details = [
        row["detail"]
        for row in db.execute(
            "SELECT detail FROM run_events WHERE run_id = ? ORDER BY id", (run_id,)
        )
    ]
    assert "reopened to build this flyer again" in details


@pytest.mark.parametrize(
    "said",
    ["run it again", "Run it again.", "can you run it again?", "hey Gable, run it again"],
)
def test_the_words_chase_actually_uses_are_recognised(said: str) -> None:
    """Asking to run it again was not in the older phrase set, so nothing happened."""
    assert asks_to_run_again(said) is True


@pytest.mark.parametrize(
    "said",
    ["here you go", "thanks", "make the price bigger", "run the numbers again for me please"],
)
def test_anything_else_falls_through_to_the_conversation(said: str) -> None:
    """This authorises replacing a delivered flyer, so it stays exact."""
    assert asks_to_run_again(said) is False


def test_a_value_supplied_after_delivery_reopens_the_finished_run(
    tmp_path: Path,
) -> None:
    """Asking to run it again with a new price answered that nothing was waiting.

    The brain reads "Can you run this again? The price should be $560,000" as a
    supplied value, which is right. But a delivered run is terminal, so the
    claim inside the resume refused it, and the most natural thing to ask after
    reading a flyer got the "already being rechecked" message instead of a new
    one. Observed live on Deborah Manarin's New Listing with Open House.
    """
    from gable.slackapp.resume import may_rebuild

    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    item = submission(rid="rid-again-value", email="deborah@cornerhouserealty.com")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    store.set_status(connection, run.run_id, "delivered", "built", slack_thread_ts="111.1")

    current = store.run_by_id(connection, run.run_id)
    assert current is not None

    assert may_rebuild(connection, current, "act-1", "111.1")
    after = store.run_by_id(connection, run.run_id)
    assert after is not None
    assert after.status not in store.TERMINAL


def test_the_same_supplied_value_delivered_twice_rebuilds_once(tmp_path: Path) -> None:
    """Slack redelivers the reply too; only one delivery may reopen the flyer."""
    from gable.slackapp.resume import may_rebuild

    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    item = submission(rid="rid-again-twice", email="deborah@cornerhouserealty.com")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    store.set_status(connection, run.run_id, "delivered", "built", slack_thread_ts="222.2")

    current = store.run_by_id(connection, run.run_id)
    assert current is not None
    first = may_rebuild(connection, current, "act-2", "222.2")
    reopened = store.run_by_id(connection, run.run_id)
    assert reopened is not None
    second = may_rebuild(connection, reopened, "act-2", "222.2")

    assert first
    assert not second


def test_a_run_still_waiting_needs_no_reopening(tmp_path: Path) -> None:
    """Only a finished flyer is terminal; a paused one continues as it always did."""
    from gable.slackapp.resume import may_rebuild

    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    item = submission(rid="rid-again-paused", email="deborah@cornerhouserealty.com")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    store.set_status(connection, run.run_id, "needs_info", "asked", slack_thread_ts="333.3")

    current = store.run_by_id(connection, run.run_id)
    assert current is not None
    assert may_rebuild(connection, current, "act-3", "333.3")
