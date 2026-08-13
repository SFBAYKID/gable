"""Transactional invariants for run state and its append-only history."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake


def _submission(connection: sqlite3.Connection) -> str:
    """Create the parent row required by the run foreign key."""
    response_id = "response-atomic"
    store.record_submission(
        connection,
        response_id,
        2,
        "8/12/2026 09:00:00",
        Intake(
            agent_email="agent@example.com",
            agent_name="Agent Example",
            request_type="Sold",
            address="1 Main St, Baltimore, MD 21201",
            post_details="",
            open_house="",
            new_price="",
            closing_price="",
            extra_notes="",
            side="",
            notes="",
        ),
        "content",
    )
    return response_id


def _db(tmp_path: Path) -> sqlite3.Connection:
    """Return one migrated database for an atomicity test."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    return connection


def test_opening_a_run_rolls_back_if_its_first_event_cannot_be_written(
    tmp_path: Path,
) -> None:
    """A run with no opening event would be impossible to explain."""
    connection = _db(tmp_path)
    response_id = _submission(connection)
    connection.execute(
        "CREATE TRIGGER reject_open_event BEFORE INSERT ON run_events "
        "WHEN NEW.status = 'pending' BEGIN SELECT RAISE(ABORT, 'reject'); END"
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.start_run(connection, response_id)

    assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_status_change_rolls_back_if_its_event_cannot_be_written(tmp_path: Path) -> None:
    """The mutable row and immutable transition ledger always agree."""
    connection = _db(tmp_path)
    run = store.start_run(connection, _submission(connection))
    connection.execute(
        "CREATE TRIGGER reject_delivery_event BEFORE INSERT ON run_events "
        "WHEN NEW.status = 'delivered' BEGIN SELECT RAISE(ABORT, 'reject'); END"
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.set_status(connection, run.run_id, "delivered", "done", output_url="example")

    current = store.run_by_id(connection, run.run_id)
    assert current is not None
    assert current.status == "pending"
    assert current.output_url == ""
    statuses = connection.execute(
        "SELECT status FROM run_events WHERE run_id = ? ORDER BY id", (run.run_id,)
    ).fetchall()
    assert [row["status"] for row in statuses] == ["pending"]


def test_only_one_worker_can_claim_a_paused_run(tmp_path: Path) -> None:
    """Two Slack events cannot both build from the same human pause."""
    connection = _db(tmp_path)
    run = store.start_run(connection, _submission(connection))
    store.set_status(connection, run.run_id, "needs_photo", "waiting")

    assert store.claim_paused_run(
        connection,
        run.run_id,
        {
            "photo_url": "https://example.test/first.jpg",
            "photo_source": "slack_upload",
        },
    )
    assert not store.claim_paused_run(
        connection,
        run.run_id,
        {
            "photo_url": "https://example.test/second.jpg",
            "photo_source": "slack_upload",
        },
    )

    current = store.run_by_id(connection, run.run_id)
    assert current is not None
    assert current.status == "pending"
    assert current.photo_url.endswith("first.jpg")


def test_concurrent_workers_cannot_exceed_the_three_attempt_ceiling(tmp_path: Path) -> None:
    """The count and insertion are one database decision, not a race."""
    path = tmp_path / "gable.db"
    connection = connect(path)
    apply_migrations(connection)
    response_id = _submission(connection)
    first = store.start_run(connection, response_id)
    store.set_status(connection, first.run_id, "failed", "first test attempt")
    second = store.start_run(connection, response_id)
    store.set_status(connection, second.run_id, "failed", "second test attempt")
    connection.close()

    def compete(_index: int) -> str:
        worker = connect(path)
        try:
            try:
                store.start_run(worker, response_id)
            except (store.RunLimitReachedError, store.RunAlreadyActiveError):
                return "refused"
            return "opened"
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(compete, range(8)))

    checked = connect(path)
    assert outcomes.count("opened") == 1
    assert store.run_attempt_count(checked, response_id) == store.MAX_RUN_ATTEMPTS
    assert checked.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 5
    checked.close()


def test_startup_recovery_explains_runs_interrupted_in_active_states(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    response_id = _submission(connection)
    pending = store.start_run(connection, response_id)
    store.set_status(connection, pending.run_id, "building", "copy created")

    assert store.recover_interrupted_runs(connection) == 1

    recovered = store.run_by_id(connection, pending.run_id)
    assert recovered is not None and recovered.status == "failed"
    events = connection.execute(
        "SELECT status, detail FROM run_events WHERE run_id = ? ORDER BY id",
        (pending.run_id,),
    ).fetchall()
    assert [event["status"] for event in events] == ["pending", "building", "failed"]
    assert "prior process ended" in events[-1]["detail"]
