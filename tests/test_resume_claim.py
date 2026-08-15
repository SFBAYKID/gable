"""Exactly one worker may continue a paused run."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.resume_claim import PHOTO_RESUME_STATES, claim_for_resume
from tests.runner_support import record, submission

THREAD = "1786821218.003929"


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """One migrated database with a single recorded submission."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    return connection


def _paused(connection: sqlite3.Connection, status: str, rid: str = "rid-resume") -> str:
    item = submission(rid=rid, ts="8/15/2026 12:13:00")
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    store.set_status(connection, run.run_id, status, "waiting", slack_thread_ts=THREAD)
    return run.run_id


@pytest.mark.parametrize("status", sorted(PHOTO_RESUME_STATES))
def test_a_photo_may_resume_either_state_that_holds_an_unsent_draft(
    db: sqlite3.Connection,
    status: str,
) -> None:
    """needs_review refused a replacement photo, which was a dead end."""
    run_id = _paused(db, status, rid=f"rid-{status}")

    claim = claim_for_resume(
        db,
        run_id,
        {"photo_url": "http://198.51.100.7/x.jpg"},
        expected_status=status,
        origin_thread_ts=THREAD,
        photo_event_id="Ev1",
    )

    assert claim.won


def test_a_run_that_moved_to_another_pause_refuses_the_photo(db: sqlite3.Connection) -> None:
    """A stale file event must not attach to a run now waiting for something else."""
    run_id = _paused(db, "needs_photo")
    store.set_status(db, run_id, "needs_info", "waiting for a direct phone")

    claim = claim_for_resume(
        db,
        run_id,
        {"photo_url": "http://198.51.100.7/x.jpg"},
        expected_status="needs_photo",
        origin_thread_ts=THREAD,
        photo_event_id="Ev1",
    )

    assert not claim.won
    assert claim.status == "needs_info"
    assert not claim.duplicate


def test_the_second_delivery_of_one_upload_loses_silently(db: sqlite3.Connection) -> None:
    """Both deliveries prepare the same image; only one may say anything."""
    run_id = _paused(db, "needs_photo")
    first = claim_for_resume(
        db,
        run_id,
        {"photo_url": "http://198.51.100.7/x.jpg", "photo_event_id": "Ev1"},
        expected_status="needs_photo",
        origin_thread_ts=THREAD,
        photo_event_id="Ev1",
    )
    assert first.won

    second = claim_for_resume(
        db,
        run_id,
        {"photo_url": "http://198.51.100.7/x.jpg", "photo_event_id": "Ev1"},
        expected_status="needs_photo",
        origin_thread_ts=THREAD,
        photo_event_id="Ev1",
    )

    assert not second.won
    # Silent: the winner is building the very same flyer and will post its link.
    assert second.duplicate


def test_a_different_upload_losing_the_race_is_not_a_duplicate(db: sqlite3.Connection) -> None:
    run_id = _paused(db, "needs_photo")
    assert claim_for_resume(
        db,
        run_id,
        {"photo_event_id": "Ev1"},
        expected_status="needs_photo",
        origin_thread_ts=THREAD,
        photo_event_id="Ev1",
    ).won

    second = claim_for_resume(
        db,
        run_id,
        {"photo_event_id": "Ev2"},
        expected_status="needs_photo",
        origin_thread_ts=THREAD,
        photo_event_id="Ev2",
    )

    assert not second.won
    assert not second.duplicate
