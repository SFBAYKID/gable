"""One ask, then the link: the contract Chase set on 2026-08-13.

Gable used to pause once per missing thing, so a flyer took six turns to start.
These tests pin the replacement: everything outstanding is asked for in a single
message, an unanswered value keeps the design's own placeholder instead of
stopping the run, and nothing is asked for twice.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gable.db.schema import apply_migrations, connect
from gable.listings.enrich import Facts
from gable.pipeline import run_reporting
from tests.runner_support import Recorder
from tests.runner_support import record as _record
from tests.runner_support import runner as _runner
from tests.runner_support import submission as _submission


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Return a migrated database on disk, as a real run uses."""
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection



def test_the_photo_and_every_missing_value_are_asked_for_in_one_message(
    db: sqlite3.Connection,
) -> None:
    """The whole point: one list, one round of answers, then the link.

    Gable used to pause once per gap. Carmen answered the square footage, was
    asked the price, answered that, and was then asked for the photo.
    """
    submission = _submission(rid="rid-one-ask")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec, facts=Facts())
    runner.hero_photo_url = ""

    result = runner.run(submission)

    assert result.status == "needs_photo", "the upload must be able to resume this run"
    assert len(rec.said) == 2, "one announcement, then exactly one question"
    asked = rec.said[1]
    assert "Can you send me the image?" in asked
    for wanted in ("beds", "baths", "square footage", "price"):
        assert wanted in asked.lower(), f"the one ask must name the {wanted}"
    assert "placeholder" in asked.lower(), "silence has to be a usable answer"


def test_an_unanswered_value_leaves_its_placeholder_and_still_delivers(
    db: sqlite3.Connection,
) -> None:
    """The user is allowed to ignore the question. The flyer still arrives."""
    submission = _submission(rid="rid-blank-delivers")
    _record(db, submission)
    asked = Recorder()
    first = _runner(db, asked, facts=Facts())
    first.hero_photo_url = ""
    paused = first.run(submission)
    assert paused.status == "needs_photo"

    # The photo lands; nobody answered any of the values.
    rec = Recorder()
    resumed = _runner(db, rec, facts=Facts())
    result = resumed.resume(submission, paused.run_id)

    assert result.status == "delivered", "a gap must not stop a finished flyer"
    assert rec.copied is True
    delivered = "\n".join(rec.said)
    assert "Open the flyer" in delivered
    assert "placeholder is still there" in delivered, "she has to know what is unfilled"


def test_a_second_pass_does_not_ask_for_the_same_values_again(
    db: sqlite3.Connection,
) -> None:
    """Asking twice with the same words is how a question becomes a dead end."""
    submission = _submission(rid="rid-no-second-ask")
    _record(db, submission)
    first = _runner(db, Recorder(), facts=Facts())
    first.hero_photo_url = ""
    paused = first.run(submission)

    rec = Recorder()
    _runner(db, rec, facts=Facts()).resume(submission, paused.run_id)

    assert not any("I still need" in said for said in rec.said)
    assert not any("Can you send me the image" in said for said in rec.said)


def test_a_contradiction_still_stops_rather_than_joining_the_batch(
    db: sqlite3.Connection,
) -> None:
    """An address that reads as a review link cannot be left as a placeholder."""
    submission = _submission(rid="rid-bad-address", address="see my google review page")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = ""

    result = runner.run(submission)

    assert result.status == "needs_info"
    assert "cannot make sense of the address" in rec.said[-1].lower()
    assert rec.copied is False, "nothing may be built from an address nobody can read"


def test_a_flyer_fitted_from_a_small_photo_invites_a_better_one(
    db: sqlite3.Connection,
) -> None:
    """The fit is as good as the source allows, and only Carmen can improve it."""
    submission = _submission(rid="rid-small-source")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)

    result = runner.run(submission)
    # The photo step records exactly what it did; the delivery message reads it.
    db.execute(
        "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
        (result.run_id, "now", "building", run_reporting.SMALL_SOURCE_DETAIL),
    )

    assert run_reporting.used_small_source_fit(db, result.run_id) is True
    message = run_reporting.delivery_message(
        db,
        result.run_id,
        output_url="http://example.test/edit",
        run_notes=[],
        advisories=[],
        left_blank=[],
    )
    assert "did my best to fit this image" in message
    assert "higher-quality version" in message


# --- values are filled the way the design draws them ------------------------


def test_square_footage_drops_a_unit_the_design_already_draws() -> None:
    """Every design puts the number beside its own ft² icon and Sq FT label."""
    from gable.pipeline.run_values import _measure_only

    assert _measure_only("1450 sq ft") == "1450"
    assert _measure_only("2,430 Sq FT") == "2,430"
    assert _measure_only("1450") == "1450"


def test_a_value_with_no_digits_is_left_as_the_person_typed_it() -> None:
    from gable.pipeline.run_values import _measure_only

    assert _measure_only("unknown") == "unknown"


def test_the_agent_title_carries_no_credential_mark() -> None:
    """New Listing draws its own superscript, so supplying one doubled it."""
    from gable.pipeline.run_values import _title_word

    assert _title_word("REALTOR®") == "REALTOR"
    assert _title_word("Realtor ®") == "Realtor"
    assert _title_word("Associate Broker") == "Associate Broker"
