"""Exact historical-row adoption cannot silently skip changed or extra work."""

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
from gable.sheets import repository as repo
from gable.sheets.identity import legacy_identity, source_identity
from tests.runner_support import submission

adopt_asserted = cast(
    Callable[[sqlite3.Connection, list[repo.Submission], list[object], str], int],
    importlib.import_module("tools.adopt_rows").adopt_asserted,
)
Assertion = importlib.import_module("tools.adopt_rows").Assertion


def _db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    repo.adopt_backfill(connection, [])
    return connection


def _row(row: int, address: str, content_hash: str) -> repo.Submission:
    item = submission(rid=source_identity("Form Responses 1", row), address=address)
    return repo.Submission(
        response_row_id=item.response_row_id,
        sheet_row=row,
        submitted_at=item.submitted_at,
        intake=item.intake,
        content_hash=content_hash,
        source_tab="Form Responses 1",
    )


def test_only_exact_asserted_rows_become_terminal_history(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    first = _row(46, "1 Historical St", "1111111111111111")
    second = _row(105, "2 Still Pending St", "2222222222222222")

    adopted = adopt_asserted(
        connection,
        [first, second],
        [Assertion(46, first.content_hash)],
        "Form Responses 1",
    )

    assert adopted == 1
    assert store.has_been_handled(connection, first.response_row_id)
    assert not store.has_been_handled(connection, second.response_row_id)
    assert store.load_submission(connection, second.response_row_id) is not None
    run = store.latest_run(connection, first.response_row_id)
    assert run is not None and run.status == "skipped"


def test_changed_assertion_rolls_back_the_complete_batch(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    first = _row(46, "1 Historical St", "1111111111111111")
    second = _row(105, "2 Historical St", "2222222222222222")

    with pytest.raises(ValueError, match="changed"):
        adopt_asserted(
            connection,
            [first, second],
            [Assertion(46, first.content_hash), Assertion(105, "ffffffffffffffff")],
            "Form Responses 1",
        )

    assert connection.execute("SELECT COUNT(*) AS n FROM submissions").fetchone()["n"] == 0
    assert connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0


def test_repeating_the_same_adoption_is_idempotent(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    item = _row(46, "1 Historical St", "1111111111111111")
    assertion = [Assertion(46, item.content_hash)]

    assert adopt_asserted(connection, [item], assertion, "Form Responses 1") == 1
    assert adopt_asserted(connection, [item], assertion, "Form Responses 1") == 0
    assert store.run_attempt_count(connection, item.response_row_id) == 1


def test_adopting_a_split_legacy_collision_preserves_the_owned_run(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    retained = _row(102, "1713 Allerford Dr", "0ef120d65a9df904")
    split = _row(103, "1713 Allerford Dr", "db31f2914f8fbbfe")
    legacy_id = legacy_identity(
        retained.submitted_at,
        retained.intake.agent_email,
        retained.intake.address,
    )
    historical = replace(retained, response_row_id=legacy_id, source_tab="")
    store.record_submission(
        connection,
        historical.response_row_id,
        historical.sheet_row,
        historical.submitted_at,
        historical.intake,
        historical.content_hash,
    )
    run = store.start_run(connection, legacy_id)
    store.set_status(
        connection,
        run.run_id,
        "needs_photo",
        "waiting for the supplied property image",
        slack_thread_ts="1786550425.479099",
    )

    adopted = adopt_asserted(
        connection,
        [retained, split],
        [Assertion(103, split.content_hash)],
        "Form Responses 1",
    )

    assert adopted == 1
    reconciled = repo.reconcile_submissions(connection, [retained, split])
    assert [item.response_row_id for item in reconciled] == [
        legacy_id,
        source_identity("Form Responses 1", 103),
    ]
    saved = store.load_submission(connection, legacy_id)
    assert saved is not None and saved.content_hash == retained.content_hash
    retained_run = store.run_by_id(connection, run.run_id)
    assert retained_run is not None
    assert retained_run.response_row_id == legacy_id
    assert retained_run.status == "needs_photo"
    assert retained_run.slack_thread_ts == "1786550425.479099"
    split_run = store.latest_run(connection, reconciled[1].response_row_id)
    assert split_run is not None and split_run.status == "skipped"


def test_adoption_does_not_apply_an_unrelated_form_edit_to_an_existing_run(
    tmp_path: Path,
) -> None:
    connection = _db(tmp_path)
    saved = _row(20, "20 Existing St", "aaaaaaaaaaaaaaaa")
    store.record_submission(
        connection,
        saved.response_row_id,
        saved.sheet_row,
        saved.submitted_at,
        saved.intake,
        saved.content_hash,
        saved.source_tab,
    )
    existing_run = store.start_run(connection, saved.response_row_id)
    store.set_status(
        connection,
        existing_run.run_id,
        "needs_review",
        "waiting for a source correction",
        slack_thread_ts="owned-existing-thread",
    )
    corrected = replace(saved, content_hash="bbbbbbbbbbbbbbbb")
    target = _row(46, "46 Historical St", "cccccccccccccccc")

    assert (
        adopt_asserted(
            connection,
            [corrected, target],
            [Assertion(46, target.content_hash)],
            "Form Responses 1",
        )
        == 1
    )

    preserved = store.load_submission(connection, saved.response_row_id)
    assert preserved is not None and preserved.content_hash == saved.content_hash
    run_after = store.run_by_id(connection, existing_run.run_id)
    assert run_after is not None
    assert run_after.response_row_id == saved.response_row_id
    assert run_after.slack_thread_ts == "owned-existing-thread"
