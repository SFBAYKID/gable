"""Releasing a run to build with unknown values left blank.

The release used to move the run to ``pending``, which put it outside the
paused states the very next step claims. Every release therefore answered "this
listing is already being rechecked" and nothing was ever built.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.pipeline import vision


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


def _paused_run(connection: sqlite3.Connection, status: str = "needs_info") -> str:
    """Create one run parked in a human-owned pause."""
    store.record_submission(
        connection,
        "response-blank",
        11,
        "response-blank",
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
    )
    run_id = store.start_run(connection, "response-blank").run_id
    store.set_status(connection, run_id, status, "waiting on a person")
    return run_id


def test_the_release_leaves_the_run_claimable(db: sqlite3.Connection) -> None:
    """The whole point: the next step is a claim, and it has to succeed."""
    run_id = _paused_run(db)

    store.approve_blank_fields(db, run_id)

    assert store.blanks_approved(db, run_id) is True
    row = db.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row["status"] == "needs_info", "the run must still be paused to be claimed"
    assert store.claim_paused_run(db, run_id) is True


def test_the_release_is_recorded_as_an_event(db: sqlite3.Connection) -> None:
    run_id = _paused_run(db)

    store.approve_blank_fields(db, run_id, "asked for everything at once")

    details = [
        row["detail"]
        for row in db.execute(
            "SELECT detail FROM run_events WHERE run_id = ? ORDER BY id", (run_id,)
        )
    ]
    assert "asked for everything at once" in details


def test_releasing_twice_changes_nothing(db: sqlite3.Connection) -> None:
    run_id = _paused_run(db)

    store.approve_blank_fields(db, run_id)
    store.approve_blank_fields(db, run_id)

    codes = db.execute(
        "SELECT approved_warning_codes FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    assert codes == store.BUILD_WITH_BLANKS


def test_an_unknown_run_is_ignored_rather_than_crashing(db: sqlite3.Connection) -> None:
    store.approve_blank_fields(db, "no-such-run")

    assert store.blanks_approved(db, "no-such-run") is False


# --- the visual gate forgives only the placeholders Gable left on purpose ---


def _seen(
    problems: list[str],
    kinds: tuple[vision.InspectionProblemKind, ...],
) -> vision.Inspection:
    return vision.Inspection(
        looks_right=False,
        confident=True,
        problems=problems,
        problem_kinds=kinds,
        remedy=vision.InspectionRemedy.REVIEW,
    )


def test_a_placeholder_only_verdict_becomes_a_pass() -> None:
    seen = _seen(
        ["A price placeholder is still showing."],
        (vision.InspectionProblemKind.PLACEHOLDER,),
    )

    filtered = seen.without_expected_placeholders()

    assert filtered.looks_right is True
    assert filtered.problems == []
    assert filtered.remedy is vision.InspectionRemedy.NONE


def test_a_real_defect_alongside_a_placeholder_still_fails() -> None:
    """The gate is not weakened; only the expected placeholder is dropped."""
    seen = _seen(
        ["A price placeholder is still showing.", "The address is clipped at the box edge."],
        (vision.InspectionProblemKind.PLACEHOLDER, vision.InspectionProblemKind.TEXT),
    )

    filtered = seen.without_expected_placeholders()

    assert filtered.looks_right is False
    assert filtered.problems == ["The address is clipped at the box edge."]
    assert filtered.problem_kinds == (vision.InspectionProblemKind.TEXT,)


def test_categories_that_do_not_line_up_forgive_nothing() -> None:
    """Nothing can be dropped safely, so the flyer still goes to a person."""
    seen = _seen(["A placeholder is showing.", "Something else is wrong."], ())

    assert seen.without_expected_placeholders() is seen
