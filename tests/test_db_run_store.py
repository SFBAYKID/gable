"""Transactional invariants for run state and its append-only history."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import SCHEMA_VERSION, apply_migrations, connect, current_version
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


def _remove_v10_template_columns(connection: sqlite3.Connection) -> None:
    """Restore template_audits to its shipped pre-v10 shape for migration tests."""
    for column in (
        "delivery_claim_token",
        "delivery_claimed_at",
        "notification_attempted_at",
        "notification_attempt_count",
    ):
        connection.execute(f"ALTER TABLE template_audits DROP COLUMN {column}")


def _remove_v10_run_columns(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE runs DROP COLUMN photo_event_id")


def _remove_v13_submission_columns(connection: sqlite3.Connection) -> None:
    """Restore submissions to its pre-v13 shape, without the content type."""
    connection.execute("ALTER TABLE submissions DROP COLUMN content_type")


def _remove_v14_run_columns(connection: sqlite3.Connection) -> None:
    """Restore runs to its pre-v14 shape, without the recorded photo ask."""
    connection.execute("ALTER TABLE runs DROP COLUMN awaiting_photo")


def _remove_v15_template_columns(connection: sqlite3.Connection) -> None:
    """Restore template_audits to its pre-v15 shape, without the refusal reason."""
    connection.execute("ALTER TABLE template_audits DROP COLUMN blocker_kind")


def test_schema_six_migrates_through_current_durable_state(
    tmp_path: Path,
) -> None:
    """A deployed v6 database gains reconciliation without changing old rows."""
    connection = _db(tmp_path)
    connection.execute("DROP TABLE submission_source_rows")
    connection.execute("DROP TABLE run_questions")
    connection.execute("DROP TABLE operation_releases")
    connection.execute("DROP TABLE slack_event_claims")
    connection.execute("DROP INDEX idx_spend_id_run")
    _remove_v10_template_columns(connection)
    _remove_v10_run_columns(connection)
    _remove_v13_submission_columns(connection)
    _remove_v14_run_columns(connection)
    _remove_v15_template_columns(connection)
    connection.execute("DELETE FROM schema_version WHERE version >= 7")
    assert current_version(connection) == 6

    assert apply_migrations(connection) == 9

    assert current_version(connection) == SCHEMA_VERSION == 15
    columns = connection.execute("PRAGMA table_info(operation_releases)").fetchall()
    assert [str(row["name"]) for row in columns] == [
        "id",
        "spend_id",
        "run_id",
        "operation_detail",
        "reason",
        "evidence",
        "at",
    ]
    outbox_columns = connection.execute("PRAGMA table_info(run_questions)").fetchall()
    assert {
        "notification_kind",
        "pending_status",
        "created_at",
        "confirmed_at",
        "satisfied_at",
        "delivery_claim_token",
        "question_attempt_count",
    }.issubset({str(row["name"]) for row in outbox_columns})


def test_deployed_schema_seven_gains_the_generalized_outbox(tmp_path: Path) -> None:
    """The current live database can apply the unshipped v8 and v9 atomically."""
    connection = _db(tmp_path)
    connection.execute("DROP TABLE submission_source_rows")
    connection.execute("DROP TABLE run_questions")
    connection.execute("DROP TABLE slack_event_claims")
    _remove_v10_template_columns(connection)
    _remove_v10_run_columns(connection)
    _remove_v13_submission_columns(connection)
    _remove_v14_run_columns(connection)
    _remove_v15_template_columns(connection)
    connection.execute("DELETE FROM schema_version WHERE version >= 8")
    assert current_version(connection) == 7

    assert apply_migrations(connection) == 8

    assert current_version(connection) == SCHEMA_VERSION == 15
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(run_questions)").fetchall()
    }
    assert {
        "notification_kind",
        "pending_status",
        "created_at",
        "satisfied_at",
        "delivery_claim_token",
        "question_attempt_count",
    } <= columns
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'slack_event_claims'"
    ).fetchone()


def test_slack_event_claim_survives_restart_and_has_one_cross_process_winner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gable.db"
    first = _db(tmp_path)
    second = connect(path)

    assert store.claim_slack_event(
        first,
        "file_share",
        "event-1",
        "run-1",
        "thread-1",
        "file-1",
    )
    assert not store.claim_slack_event(
        second,
        "file_share",
        "event-1",
        "run-1",
        "thread-1",
        "file-1",
    )
    first.close()

    restarted = connect(path)
    assert store.slack_event_claimed(restarted, "file_share", "event-1")
    assert store.complete_slack_event(
        restarted,
        "file_share",
        "event-1",
        "run-1",
        "photo handoff stored its durable result",
    )
    assert not store.complete_slack_event(
        restarted,
        "file_share",
        "event-1",
        "run-1",
        "duplicate completion",
    )


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


def test_a_stale_photo_event_cannot_claim_a_different_paused_state(tmp_path: Path) -> None:
    """The status observed before download is part of the atomic resume claim."""
    connection = _db(tmp_path)
    run = store.start_run(connection, _submission(connection))
    store.set_status(connection, run.run_id, "needs_photo", "waiting for the image")

    # Another human action resolves the photo wait but discovers missing data
    # while the file worker still holds its earlier needs_photo observation.
    store.set_status(connection, run.run_id, "needs_info", "waiting for a direct phone")

    assert not store.claim_paused_run(
        connection,
        run.run_id,
        {"photo_url": "https://example.test/stale.jpg"},
        expected_status="needs_photo",
    )
    current = store.run_by_id(connection, run.run_id)
    assert current is not None
    assert current.status == "needs_info"
    assert current.photo_url == ""


def test_an_expected_claim_status_must_itself_be_paused(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    run = store.start_run(connection, _submission(connection))

    with pytest.raises(ValueError, match="expected status is not paused"):
        store.claim_paused_run(connection, run.run_id, expected_status="building")


@pytest.mark.parametrize("paused_status", sorted(store.PAUSED))
def test_a_paused_run_blocks_a_new_attempt(tmp_path: Path, paused_status: str) -> None:
    """Human-owned pauses resume in place and never consume another attempt."""
    connection = _db(tmp_path)
    response_id = _submission(connection)
    run = store.start_run(connection, response_id)
    store.set_status(connection, run.run_id, paused_status, "waiting on a person")

    with pytest.raises(store.RunAlreadyActiveError, match="active or paused"):
        store.start_run(connection, response_id)

    assert store.run_attempt_count(connection, response_id) == 1
    assert store.latest_run(connection, response_id) == store.run_by_id(connection, run.run_id)


def test_unknown_status_is_rejected_without_mutating_the_run_or_event_log(
    tmp_path: Path,
) -> None:
    """A spelling mistake cannot create a state the poller will never revisit."""
    connection = _db(tmp_path)
    run = store.start_run(connection, _submission(connection))

    with pytest.raises(ValueError, match="unknown run status"):
        store.set_status(connection, run.run_id, "needz_photo", "misspelled")

    current = store.run_by_id(connection, run.run_id)
    assert current is not None and current.status == "pending"
    events = connection.execute(
        "SELECT status FROM run_events WHERE run_id = ? ORDER BY id", (run.run_id,)
    ).fetchall()
    assert [event["status"] for event in events] == ["pending"]


def test_legacy_warning_fields_remain_readable_but_runtime_cannot_mutate_them(
    tmp_path: Path,
) -> None:
    """Migration-era warning columns are inert append-only compatibility data."""
    connection = _db(tmp_path)
    run = store.start_run(connection, _submission(connection))
    connection.execute(
        "UPDATE runs SET approved_warning_codes = ?, pending_warning_code = ? WHERE run_id = ?",
        ("address_tight", "hero_crop", run.run_id),
    )
    store.set_status(
        connection,
        run.run_id,
        "needs_template",
        "waiting for a measured warning decision",
    )

    current = store.run_by_id(connection, run.run_id)
    assert current is not None
    assert current.approved_warning_codes == "address_tight"
    assert current.pending_warning_code == "hero_crop"


def test_submission_source_tab_is_added_without_erasing_the_saved_payload(
    tmp_path: Path,
) -> None:
    connection = _db(tmp_path)
    response_id = _submission(connection)
    before = store.load_submission(connection, response_id)
    assert before is not None and before.source_tab == ""

    store.record_submission(
        connection,
        response_id,
        before.sheet_row,
        before.submitted_at,
        before.intake,
        source_tab="Testing_1",
    )

    after = store.load_submission(connection, response_id)
    assert after is not None
    assert after.source_tab == "Testing_1"
    assert after.content_hash == before.content_hash
    assert after.intake == before.intake


def test_startup_refuses_a_database_created_by_newer_code(tmp_path: Path) -> None:
    """An old binary must not run against columns or invariants it cannot know."""
    connection = _db(tmp_path)
    connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION + 1,))

    with pytest.raises(RuntimeError, match="newer than this code supports"):
        apply_migrations(connection)

    assert current_version(connection) == SCHEMA_VERSION + 1


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

    notices = store.recover_interrupted_runs(connection)

    recovered = store.run_by_id(connection, pending.run_id)
    assert [notice.run_id for notice in notices] == [pending.run_id]
    assert recovered is not None and recovered.status == "failed"
    assert recovered.failure_reason == store.INTERRUPTED_NOTIFICATION_PENDING
    events = connection.execute(
        "SELECT status, detail FROM run_events WHERE run_id = ? ORDER BY id",
        (pending.run_id,),
    ).fetchall()
    assert [event["status"] for event in events] == ["pending", "building", "failed"]
    assert "prior process ended" in events[-1]["detail"]


def test_threaded_interruption_pauses_the_same_attempt_until_a_person_resumes(
    tmp_path: Path,
) -> None:
    """An owned listing thread makes interrupted work safely resumable."""
    connection = _db(tmp_path)
    response_id = _submission(connection)
    run = store.start_run(connection, response_id)
    store.set_status(
        connection,
        run.run_id,
        "building",
        "photo placed",
        slack_thread_ts="1786468156.700001",
        output_file_id="output-one",
        output_url="https://example.test/output-one",
    )

    notices = store.recover_interrupted_runs(connection)

    recovered = store.run_by_id(connection, run.run_id)
    assert [notice.run_id for notice in notices] == [run.run_id]
    assert recovered is not None
    assert recovered.status == "needs_review"
    assert recovered.output_file_id == "output-one"
    assert recovered.failure_reason == store.INTERRUPTED_NOTIFICATION_PENDING
    assert store.run_attempt_count(connection, response_id) == 1

    # Until Slack confirms the recovery message, another startup owes the same
    # notice rather than silently treating it as delivered.
    assert [notice.run_id for notice in store.recover_interrupted_runs(connection)] == [run.run_id]
    assert store.acknowledge_interrupted_run(connection, run.run_id, "1786468156.700099")
    acknowledged = store.run_by_id(connection, run.run_id)
    assert acknowledged is not None
    assert acknowledged.status == "needs_review"
    assert acknowledged.slack_thread_ts == "1786468156.700001"
    assert acknowledged.failure_reason == store.INTERRUPTED_REASON
    assert store.recover_interrupted_runs(connection) == ()

    assert store.claim_paused_run(connection, run.run_id)
    assert store.run_attempt_count(connection, response_id) == 1


def test_unthreaded_recovery_notice_becomes_the_failed_run_thread(tmp_path: Path) -> None:
    """The channel notice gives an otherwise silent failure a durable place to discuss."""
    connection = _db(tmp_path)
    run = store.start_run(connection, _submission(connection))

    notices = store.recover_interrupted_runs(connection)
    assert [notice.run_id for notice in notices] == [run.run_id]
    assert notices[0].status == "failed"

    assert store.acknowledge_interrupted_run(connection, run.run_id, "1786468156.800001")
    acknowledged = store.run_by_id(connection, run.run_id)
    assert acknowledged is not None
    assert acknowledged.status == "failed"
    assert acknowledged.slack_thread_ts == "1786468156.800001"
    assert acknowledged.failure_reason == store.INTERRUPTED_REASON
    assert not store.acknowledge_interrupted_run(connection, run.run_id, "1786468156.800002")
    assert store.recover_interrupted_runs(connection) == ()
