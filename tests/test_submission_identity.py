"""Submission identity must survive real Sheets behavior without replaying work."""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.sheets import repository as repo
from gable.sheets.client import SheetError
from gable.sheets.identity import legacy_identity, source_identity

read_one = cast(
    Callable[[object, str, int, sqlite3.Connection | None], repo.Submission],
    importlib.import_module("tools.run_row").read_one,
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    return connection


def _submission(
    row: int,
    address: str,
    *,
    timestamp: str = "8/13/2026 09:00:00",
    content: str | None = None,
    tab: str = "Form Responses 1",
) -> repo.Submission:
    intake = Intake(
        agent_email="agent@cornerhouserealty.com",
        agent_name="Test Agent",
        request_type="Sold",
        address=address,
        post_details="",
        open_house="",
        new_price="",
        closing_price="615000",
        extra_notes="",
        side="Seller",
        notes="",
    )
    return repo.Submission(
        response_row_id=source_identity(tab, row),
        sheet_row=row,
        submitted_at=timestamp,
        intake=intake,
        content_hash=content or f"content-{address}",
        source_tab=tab,
    )


def _adopt(db: sqlite3.Connection, submissions: list[repo.Submission]) -> list[str]:
    ids = [item.response_row_id for item in submissions]
    assert repo.adopt_backfill(db, submissions) == len(submissions)
    return ids


def test_same_timestamp_distinct_rows_are_distinct_new_submissions(
    db: sqlite3.Connection,
) -> None:
    _adopt(db, [])
    first = _submission(2, "1 First St")
    second = _submission(3, "2 Second St")

    pending = repo.new_submissions(db, [first, second])

    assert [item.intake.address for item in pending] == ["1 First St", "2 Second St"]
    assert len({item.response_row_id for item in pending}) == 2


def test_a_single_sourced_row_cannot_bypass_complete_snapshot_reconciliation(
    db: sqlite3.Connection,
) -> None:
    with pytest.raises(SheetError, match="complete tab snapshot"):
        repo.reconcile_identity(db, _submission(2, "1 First St"))


def test_byte_identical_rows_are_distinct_when_first_seen(db: sqlite3.Connection) -> None:
    _adopt(db, [])
    first = _submission(2, "1 First St", content="identical")
    second = _submission(3, "1 First St", content="identical")

    pending = repo.new_submissions(db, [first, second])

    assert len(pending) == 2
    assert pending[0].response_row_id != pending[1].response_row_id


def test_inserting_a_row_above_does_not_replay_every_later_submission(
    db: sqlite3.Connection,
) -> None:
    originals = [
        _submission(2, "1 First St"),
        _submission(3, "2 Second St"),
        _submission(4, "3 Third St"),
    ]
    original_ids = _adopt(db, originals)
    shifted = [
        _submission(2, "New Top Row"),
        _submission(3, "1 First St"),
        _submission(4, "2 Second St"),
        _submission(5, "3 Third St"),
    ]

    pending = repo.new_submissions(db, shifted)

    assert [item.intake.address for item in pending] == ["New Top Row"]
    reconciled = repo.reconcile_submissions(db, shifted)
    assert [item.response_row_id for item in reconciled[1:]] == original_ids
    assert reconciled[0].response_row_id not in set(original_ids)
    for response_id, expected_row in zip(original_ids, (3, 4, 5), strict=True):
        saved = store.load_submission(db, response_id)
        assert saved is not None and saved.sheet_row == expected_row


def test_deletion_and_reorder_preserve_the_surviving_ids(
    db: sqlite3.Connection,
) -> None:
    originals = [
        _submission(2, "1 First St"),
        _submission(3, "2 Deleted St"),
        _submission(4, "3 Third St"),
    ]
    original_ids = _adopt(db, originals)
    current = [
        _submission(2, "3 Third St"),
        _submission(3, "1 First St"),
    ]

    assert repo.new_submissions(db, current) == []
    reconciled = repo.reconcile_submissions(db, current)
    assert [item.response_row_id for item in reconciled] == [original_ids[2], original_ids[0]]


def test_same_timestamp_ambiguous_edits_fail_closed(db: sqlite3.Connection) -> None:
    originals = [
        _submission(2, "1 First St", content="old-first"),
        _submission(3, "2 Second St", content="old-second"),
    ]
    _adopt(db, originals)
    ambiguous = [
        _submission(8, "Both Fields Changed A", content="new-a"),
        _submission(9, "Both Fields Changed B", content="new-b"),
    ]

    with pytest.raises(SheetError, match="share a timestamp"):
        repo.reconcile_submissions(db, ambiguous)


def test_legacy_identical_rows_do_not_replay_after_identity_migration(
    db: sqlite3.Connection,
) -> None:
    current = [
        _submission(2, "1 First St", content="identical"),
        _submission(3, "1 First St", content="identical"),
    ]
    legacy_id = legacy_identity(
        current[0].submitted_at,
        current[0].intake.agent_email,
        current[0].intake.address,
    )
    legacy = replace(
        current[0],
        response_row_id=legacy_id,
        source_tab="",
    )
    store.record_submission(
        db,
        legacy.response_row_id,
        legacy.sheet_row,
        legacy.submitted_at,
        legacy.intake,
        legacy.content_hash,
    )
    run = store.start_run(db, legacy.response_row_id)
    store.set_status(db, run.run_id, "skipped", "legacy historical response")
    _adopt(db, [])

    assert repo.new_submissions(db, current) == []
    reconciled = repo.reconcile_submissions(db, current)
    assert [item.response_row_id for item in reconciled] == [legacy_id, legacy_id]
    assert db.execute("SELECT COUNT(*) AS n FROM submissions").fetchone()["n"] == 1


def test_legacy_tuple_collision_keeps_distinct_payloads_as_distinct_rows(
    db: sqlite3.Connection,
) -> None:
    """The live Sheet has four pairs with one old key but different answers."""
    current = [
        _submission(12, "1 First St", content="retained-legacy-payload"),
        _submission(13, "1 First St", content="later-distinct-payload"),
    ]
    legacy_id = legacy_identity(
        current[0].submitted_at,
        current[0].intake.agent_email,
        current[0].intake.address,
    )
    # Production retained the first physical row in each collided pair.  The
    # August 12 pair also has an owned, paused Slack run on that legacy id.
    legacy = replace(current[0], response_row_id=legacy_id, source_tab="")
    store.record_submission(
        db,
        legacy.response_row_id,
        legacy.sheet_row,
        legacy.submitted_at,
        legacy.intake,
        legacy.content_hash,
    )
    run = store.start_run(db, legacy.response_row_id)
    store.set_status(
        db,
        run.run_id,
        "needs_photo",
        "waiting for the supplied property image",
        slack_thread_ts="1786550425.479099",
    )
    _adopt(db, [])

    pending = repo.new_submissions(db, current)

    assert [item.sheet_row for item in pending] == [13]
    assert pending[0].response_row_id == source_identity("Form Responses 1", 13)
    reconciled = repo.reconcile_submissions(db, current)
    assert reconciled[0].response_row_id == legacy_id
    assert reconciled[1].response_row_id == pending[0].response_row_id
    retained = store.load_submission(db, legacy_id)
    split = store.load_submission(db, pending[0].response_row_id)
    assert retained is not None and retained.content_hash == "retained-legacy-payload"
    assert split is not None and split.content_hash == "later-distinct-payload"
    retained_run = store.run_by_id(db, run.run_id)
    assert retained_run is not None
    assert retained_run.response_row_id == legacy_id
    assert retained_run.status == "needs_photo"
    assert retained_run.slack_thread_ts == "1786550425.479099"

    # A second complete read is stable.  The split row remains the same
    # unhandled identity until an operator explicitly adopts or starts it.
    again = repo.new_submissions(db, current)
    assert [(item.sheet_row, item.response_row_id) for item in again] == [
        (13, pending[0].response_row_id)
    ]


def test_exact_legacy_multiplicity_does_not_absorb_a_distinct_payload(
    db: sqlite3.Connection,
) -> None:
    current = [
        _submission(12, "1 First St", content="distinct-payload"),
        _submission(13, "1 First St", content="legacy-identical"),
        _submission(14, "1 First St", content="legacy-identical"),
    ]
    legacy_id = legacy_identity(
        current[0].submitted_at,
        current[0].intake.agent_email,
        current[0].intake.address,
    )
    legacy = replace(current[1], response_row_id=legacy_id, source_tab="")
    store.record_submission(
        db,
        legacy.response_row_id,
        legacy.sheet_row,
        legacy.submitted_at,
        legacy.intake,
        legacy.content_hash,
    )
    run = store.start_run(db, legacy.response_row_id)
    store.set_status(db, run.run_id, "skipped", "legacy historical response")
    _adopt(db, [])

    pending = repo.new_submissions(db, current)
    reconciled = repo.reconcile_submissions(db, current)

    assert [item.sheet_row for item in pending] == [12]
    assert [item.response_row_id for item in reconciled[1:]] == [legacy_id, legacy_id]
    assert reconciled[0].response_row_id == source_identity("Form Responses 1", 12)


def test_unprovable_legacy_tuple_collision_fails_closed(
    db: sqlite3.Connection,
) -> None:
    current = [
        _submission(12, "1 First St", content="first-current-payload"),
        _submission(13, "1 First St", content="second-current-payload"),
    ]
    legacy_id = legacy_identity(
        current[0].submitted_at,
        current[0].intake.agent_email,
        current[0].intake.address,
    )
    legacy = replace(
        _submission(99, "1 First St", content="unknown-old-payload"),
        response_row_id=legacy_id,
        source_tab="",
    )
    store.record_submission(
        db,
        legacy.response_row_id,
        legacy.sheet_row,
        legacy.submitted_at,
        legacy.intake,
        legacy.content_hash,
    )
    run = store.start_run(db, legacy.response_row_id)
    store.set_status(db, run.run_id, "skipped", "legacy historical response")
    _adopt(db, [])

    with pytest.raises(SheetError, match="deployed submission id covers distinct"):
        repo.reconcile_submissions(db, current)


def test_new_identical_row_after_legacy_snapshot_is_detected_once(
    db: sqlite3.Connection,
) -> None:
    historical = [
        _submission(2, "1 First St", content="identical"),
        _submission(3, "1 First St", content="identical"),
    ]
    legacy_id = legacy_identity(
        historical[0].submitted_at,
        historical[0].intake.agent_email,
        historical[0].intake.address,
    )
    legacy = replace(historical[0], response_row_id=legacy_id, source_tab="")
    store.record_submission(
        db,
        legacy.response_row_id,
        legacy.sheet_row,
        legacy.submitted_at,
        legacy.intake,
        legacy.content_hash,
    )
    run = store.start_run(db, legacy.response_row_id)
    store.set_status(db, run.run_id, "skipped", "legacy historical response")
    _adopt(db, [])
    assert repo.new_submissions(db, historical) == []

    appended = [*historical, _submission(4, "1 First St", content="identical")]
    first_poll = repo.new_submissions(db, appended)

    assert len(first_poll) == 1
    assert first_poll[0].sheet_row == 4
    assert first_poll[0].response_row_id != legacy_id
    new_id = first_poll[0].response_row_id
    new_run = store.start_run(db, new_id)
    store.set_status(db, new_run.run_id, "needs_photo", "waiting for its image")

    assert repo.new_submissions(db, appended) == []
    reconciled = repo.reconcile_submissions(db, appended)
    assert [item.response_row_id for item in reconciled] == [legacy_id, legacy_id, new_id]


def test_deleted_legacy_duplicate_is_retired_before_an_identical_reappears(
    db: sqlite3.Connection,
) -> None:
    historical = [
        _submission(2, "1 First St", content="identical"),
        _submission(3, "1 First St", content="identical"),
    ]
    legacy_id = legacy_identity(
        historical[0].submitted_at,
        historical[0].intake.agent_email,
        historical[0].intake.address,
    )
    legacy = replace(historical[0], response_row_id=legacy_id, source_tab="")
    store.record_submission(
        db,
        legacy.response_row_id,
        legacy.sheet_row,
        legacy.submitted_at,
        legacy.intake,
        legacy.content_hash,
    )
    run = store.start_run(db, legacy.response_row_id)
    store.set_status(db, run.run_id, "skipped", "legacy historical response")
    _adopt(db, [])
    assert repo.new_submissions(db, historical) == []

    surviving = [historical[0]]
    assert repo.new_submissions(db, surviving) == []
    active = db.execute(
        "SELECT COUNT(*) AS n FROM submission_source_rows WHERE active = 1"
    ).fetchone()
    assert active["n"] == 1

    reappeared = [*surviving, historical[1]]
    pending = repo.new_submissions(db, reappeared)

    assert len(pending) == 1
    assert pending[0].sheet_row == 3
    assert pending[0].response_row_id != legacy_id
    new_run = store.start_run(db, pending[0].response_row_id)
    store.set_status(db, new_run.run_id, "needs_photo", "waiting for its image")
    assert repo.new_submissions(db, reappeared) == []


def test_empty_tab_retires_aliases_before_a_later_identical_response(
    db: sqlite3.Connection,
) -> None:
    historical = _submission(2, "1 First St", content="identical")
    legacy_id = legacy_identity(
        historical.submitted_at,
        historical.intake.agent_email,
        historical.intake.address,
    )
    legacy = replace(historical, response_row_id=legacy_id, source_tab="")
    store.record_submission(
        db,
        legacy.response_row_id,
        legacy.sheet_row,
        legacy.submitted_at,
        legacy.intake,
        legacy.content_hash,
    )
    run = store.start_run(db, legacy.response_row_id)
    store.set_status(db, run.run_id, "skipped", "legacy historical response")
    _adopt(db, [])
    assert repo.new_submissions(db, [historical]) == []

    assert repo.new_submissions(db, [], source_tab="Form Responses 1") == []
    active = db.execute(
        "SELECT COUNT(*) AS n FROM submission_source_rows WHERE active = 1"
    ).fetchone()
    assert active["n"] == 0

    later = _submission(2, "1 First St", content="identical")
    pending = repo.new_submissions(db, [later], source_tab="Form Responses 1")

    assert len(pending) == 1
    assert pending[0].response_row_id != legacy_id


def test_empty_tab_does_not_resurrect_a_retired_row_derived_identity(
    db: sqlite3.Connection,
) -> None:
    historical = _submission(2, "1 First St", content="identical")
    historical_id = historical.response_row_id
    _adopt(db, [historical])

    assert repo.new_submissions(db, [], source_tab="Form Responses 1") == []
    later = _submission(2, "1 First St", content="identical")
    first_poll = repo.new_submissions(db, [later], source_tab="Form Responses 1")

    assert len(first_poll) == 1
    assert first_poll[0].response_row_id != historical_id
    new_id = first_poll[0].response_row_id
    run = store.start_run(db, new_id)
    store.set_status(db, run.run_id, "needs_photo", "waiting for its image")
    assert repo.new_submissions(db, [later], source_tab="Form Responses 1") == []
    assert repo.reconcile_submissions(db, [later])[0].response_row_id == new_id


def test_manual_row_selection_uses_the_new_identical_rows_own_id(
    db: sqlite3.Connection,
) -> None:
    historical = [
        _submission(2, "1 First St", content="identical"),
        _submission(3, "1 First St", content="identical"),
    ]
    legacy_id = legacy_identity(
        historical[0].submitted_at,
        historical[0].intake.agent_email,
        historical[0].intake.address,
    )
    legacy = replace(historical[0], response_row_id=legacy_id, source_tab="")
    store.record_submission(
        db,
        legacy.response_row_id,
        legacy.sheet_row,
        legacy.submitted_at,
        legacy.intake,
        legacy.content_hash,
    )
    run = store.start_run(db, legacy.response_row_id)
    store.set_status(db, run.run_id, "skipped", "legacy historical response")
    _adopt(db, [])
    assert repo.new_submissions(db, historical) == []

    rows = [
        [
            "Timestamp",
            "Email Address",
            "Name of Agent",
            "Select your request type",
            "Property Address",
        ],
        [
            historical[0].submitted_at,
            historical[0].intake.agent_email,
            "Test Agent",
            "Sold",
            "1 First St",
        ],
        [
            historical[1].submitted_at,
            historical[1].intake.agent_email,
            "Test Agent",
            "Sold",
            "1 First St",
        ],
        [
            historical[1].submitted_at,
            historical[1].intake.agent_email,
            "Test Agent",
            "Sold",
            "1 First St",
        ],
    ]

    class Sheet:
        def read(self, _range: str) -> list[list[str]]:
            return rows

    parsed = repo.read_submissions(Sheet(), "Form Responses 1")
    appended = repo.new_submissions(db, parsed)
    assert len(appended) == 1
    new_id = appended[0].response_row_id

    selected = read_one(Sheet(), "Form Responses 1", 4, db)

    assert selected.sheet_row == 4
    assert selected.response_row_id == new_id
    assert selected.response_row_id != legacy_id


def test_legacy_distinct_same_time_rows_reconcile_without_merging(
    db: sqlite3.Connection,
) -> None:
    current = [_submission(5, "1 First St"), _submission(6, "2 Second St")]
    legacy_ids: list[str] = []
    for item in current:
        response_id = legacy_identity(
            item.submitted_at,
            item.intake.agent_email,
            item.intake.address,
        )
        legacy_ids.append(response_id)
        store.record_submission(
            db,
            response_id,
            item.sheet_row - 2,
            item.submitted_at,
            item.intake,
            item.content_hash,
        )
        run = store.start_run(db, response_id)
        store.set_status(db, run.run_id, "skipped", "legacy historical response")
    _adopt(db, [])

    assert repo.new_submissions(db, current) == []
    assert [item.response_row_id for item in repo.reconcile_submissions(db, current)] == legacy_ids
