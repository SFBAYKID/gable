"""Tests for the one module that performs a run.

Every outside call is injected, so the whole sequence is exercised without
Google, Slack or a paid call. The properties under test are the ones that make
it safe to run unattended: every exit records a status, and nothing is guessed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gable.db.schema import apply_migrations, connect
from gable.listings.enrich import Facts
from gable.pipeline.vision import Inspection
from tests.runner_support import Recorder
from tests.runner_support import record as _record
from tests.runner_support import runner as _runner
from tests.runner_support import submission as _submission


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


# --- the happy path ---------------------------------------------------------


def test_a_complete_listing_is_built_and_delivered(db: sqlite3.Connection) -> None:
    submission = _submission()
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)

    assert result.status == "delivered"
    assert rec.copied is True
    assert result.output_url.endswith("/edit")
    assert any("Open the flyer" in said for said in rec.said)


def test_a_resumed_delivery_preserves_the_root_thread_timestamp(
    db: sqlite3.Connection,
) -> None:
    submission = _submission(rid="rid-delivered-thread")
    _record(db, submission)
    runner = _runner(db, Recorder())
    runner.origin_thread_ts = "1786468156.701419"

    result = runner.run(submission)

    row = db.execute(
        "SELECT slack_thread_ts FROM runs WHERE run_id = ?", (result.run_id,)
    ).fetchone()
    assert row["slack_thread_ts"] == "1786468156.701419"


def test_researched_facts_reach_the_flyer(db: sqlite3.Connection) -> None:
    """Beds, baths and square footage are looked up, not asked for."""
    submission = _submission()
    _record(db, submission)
    rec = Recorder()
    _runner(db, rec).run(submission)

    assert rec.filled["[ 4 BEDS ]"] == "4"
    assert rec.filled["[ SQFT ]"] == "1,804"
    assert rec.filled["[PROPERTY ADDRESS]"] == "7940 Oakwood Rd, Glen Burnie, MD 21061"


def test_the_agent_phone_comes_from_the_roster(db: sqlite3.Connection) -> None:
    submission = _submission()
    _record(db, submission)
    rec = Recorder()
    _runner(db, rec).run(submission)
    assert rec.filled["Phone"] == "(443) 854-8554"


def test_researched_facts_are_cached_for_next_time(db: sqlite3.Connection) -> None:
    """The same property comes back as a listing, an open house and a sale."""
    submission = _submission()
    _record(db, submission)
    _runner(db, Recorder()).run(submission)

    from gable.db import store

    assert store.recall_facts(db, "7940 Oakwood Rd, Glen Burnie, MD 21061")["beds"] == "4"


# --- it asks rather than guessing -------------------------------------------


def test_sold_with_no_closing_price_builds_and_offers_to_add_it(
    db: sqlite3.Connection,
) -> None:
    """Chase's rule, 2026-08-12: the link first, the missing price after it.

    Stopping first meant a flyer that was otherwise complete — photo, agent,
    address, design — waited on a number the agent could supply in seconds.
    """
    submission = _submission(request_type="Sold", rid="rid-sold")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = "http://example.invalid/hero.jpg"
    result = runner.run(submission)

    assert result.status == "delivered"
    assert rec.copied is True, "a missing price must not stop the build"
    assert any("no price" in said.lower() for said in result.said)
    assert any("give me the price" in said.lower() for said in result.said)


def test_a_resumed_question_preserves_the_root_thread_timestamp(
    db: sqlite3.Connection,
) -> None:
    submission = _submission(address="Google Review", rid="rid-question-thread")
    _record(db, submission)
    runner = _runner(db, Recorder())
    runner.origin_thread_ts = "1786468156.701419"

    result = runner.run(submission)

    row = db.execute(
        "SELECT slack_thread_ts FROM runs WHERE run_id = ?", (result.run_id,)
    ).fetchone()
    assert result.status == "needs_info"
    assert row["slack_thread_ts"] == "1786468156.701419"


def test_an_unusable_address_stops_and_asks(db: sqlite3.Connection) -> None:
    submission = _submission(address="Google Review", rid="rid-bad")
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)

    assert result.status == "needs_info"
    assert rec.copied is False


def test_research_that_finds_nothing_asks(db: sqlite3.Connection) -> None:
    submission = _submission(rid="rid-nofacts")
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec, facts=Facts()).run(submission)

    assert result.status == "needs_info"
    assert "could not find" in rec.said[0].lower()


def test_one_named_agent_is_not_ambiguous_and_still_builds(db: sqlite3.Connection) -> None:
    """Only two names with unclear roles is a question."""
    submission = _submission(details="Listed by: Stacey Abbott", rid="rid-two")
    _record(db, submission)
    assert _runner(db, Recorder()).run(submission).status == "delivered"


def test_two_agents_with_unclear_roles_stops_and_asks(db: sqlite3.Connection) -> None:
    """Row 84's shape, but with the roles left ambiguous."""
    submission = _submission(
        details="Listed by: Stacey Abbott. Co-listed by: Jason Vetter", rid="rid-amb"
    )
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)
    assert result.status == "needs_info"
    assert "listing agent" in rec.said[-1].lower()
    assert rec.copied is False


def test_two_agents_with_clear_roles_stop_until_the_layout_is_certified(
    db: sqlite3.Connection,
) -> None:
    """Page order is not proof of which person's text and photo belong together."""
    submission = _submission(
        request_type="Open House",
        details="Listed by: Stacey Abbott. Hosted by: Jason Vetter",
        open_house="Saturday, August 15 from 1 to 3 PM",
        rid="rid-two-clear",
    )
    _record(db, submission)
    rec = Recorder()

    result = _runner(db, rec).run(submission)

    assert result.status == "needs_template"
    assert "not certified" in rec.said[-1].lower()
    assert "listing agent" in rec.said[-1].lower()
    assert "hosting agent" in rec.said[-1].lower()
    assert rec.copied is False


# --- the two quality passes -------------------------------------------------


def test_a_flyer_that_still_shows_a_placeholder_is_not_delivered(
    db: sqlite3.Connection,
) -> None:
    """Delivering something with a visible token is the failure to prevent."""
    submission = _submission(rid="rid-bad-render")
    _record(db, submission)

    class StubbornFill(Recorder):
        """A client whose fill silently changes nothing."""

        def fill(self, file_id: str, pairs: dict[str, str]) -> int:  # noqa: ARG002
            """Record the pairs but leave the slide as it was."""
            self.filled = pairs
            self.output_text = list(self.slide_text)
            return 0

    rec = StubbornFill()
    result = _runner(db, rec).run(submission)
    assert result.status == "needs_review"
    assert any("did not match exactly once" in said for said in rec.said)


# --- every exit records a status --------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "delivered"),
        ({"request_type": "Sold"}, "delivered"),
        ({"address": "Google Review"}, "needs_info"),
    ],
)
def test_every_path_reaches_a_recorded_status(
    db: sqlite3.Connection, kwargs: dict[str, str], expected: str
) -> None:
    """AGENTS.md 6: a listing's state must be explainable from the log."""
    submission = _submission(rid=f"rid-{expected}-{len(kwargs)}", **kwargs)
    _record(db, submission)
    result = _runner(db, Recorder()).run(submission)
    assert result.status == expected

    row = db.execute("SELECT status FROM runs WHERE run_id = ?", (result.run_id,)).fetchone()
    assert row["status"] == expected
    events = db.execute(
        "SELECT COUNT(*) AS n FROM run_events WHERE run_id = ?", (result.run_id,)
    ).fetchone()["n"]
    assert events >= 2, "opening and the outcome must both be logged"


def test_an_unexpected_failure_is_recorded_not_raised(db: sqlite3.Connection) -> None:
    """Raising would leave the database disagreeing with reality."""
    submission = _submission(rid="rid-boom")
    _record(db, submission)

    class Exploding(Recorder):
        def copy(self, template_id: str, name: str) -> tuple[str, str]:  # noqa: ARG002  # noqa: ARG002
            """Fail the way a Drive outage would."""
            msg = "drive is down"
            raise RuntimeError(msg)

    result = _runner(db, Exploding()).run(submission)
    assert result.status == "failed"
    assert (
        db.execute("SELECT status FROM runs WHERE run_id = ?", (result.run_id,)).fetchone()[
            "status"
        ]
        == "failed"
    )


def test_a_fourth_attempt_is_refused_before_external_work(db: sqlite3.Connection) -> None:
    """The three-attempt ceiling stops retry storms before another paid call."""
    from gable.db import store

    submission = _submission(rid="rid-retry-limit")
    _record(db, submission)
    for attempt in range(store.MAX_RUN_ATTEMPTS):
        run = store.start_run(db, submission.response_row_id)
        store.set_status(db, run.run_id, "failed", f"attempt {attempt + 1} failed")

    rec = Recorder()
    result = _runner(db, rec).run(submission)

    assert result.status == "failed"
    assert rec.copied is False
    assert store.run_attempt_count(db, submission.response_row_id) == store.MAX_RUN_ATTEMPTS
    assert any("three times" in said for said in rec.said)


def test_nothing_gable_says_breaks_the_house_style(db: sqlite3.Connection) -> None:
    from gable.slackapp.style import violations

    for kwargs in ({}, {"request_type": "Sold"}, {"address": "Google Review"}):
        submission = _submission(rid=f"rid-style-{abs(hash(str(kwargs)))}", **kwargs)
        _record(db, submission)
        rec = Recorder()
        _runner(db, rec).run(submission)
        for said in rec.said:
            assert not violations(said), (said, violations(said))


def test_a_template_without_a_field_does_not_fail_the_check(db: sqlite3.Connection) -> None:
    """Judging against values the design has no slot for would fail every render.

    The email is not on this template. That is the design's choice, not a defect,
    and it must not stop delivery.
    """
    submission = _submission(rid="rid-noemail")
    _record(db, submission)
    rec = Recorder(slide_text=["[PROPERTY ADDRESS]", "AGENT NAME"])
    result = _runner(db, rec).run(submission)
    assert result.status == "delivered"


def test_a_missing_agent_phone_stays_missing_instead_of_using_the_office_line(
    db: sqlite3.Connection,
) -> None:
    """A plausible fallback phone is still the wrong phone on a client flyer."""
    from gable.pipeline import run_values

    submission = _submission(rid="rid-no-agent-phone", email="new.agent@example.test")
    values = run_values.for_intake(db, submission.intake, {})

    assert values["agent_phone"] == ""
    assert values["agent_phone"] != run_values.OFFICE_PHONE


def test_a_design_without_a_headshot_slot_can_still_be_delivered(
    db: sqlite3.Connection,
) -> None:
    """No slot is a design choice, distinct from a failed replacement."""
    submission = _submission(rid="rid-no-headshot-slot")
    _record(db, submission)
    runner = _runner(db, Recorder())
    db.execute(
        "UPDATE salespeople SET headshot_url = ? WHERE email = ?",
        ("http://example.invalid/lolo.jpg", "lolo@cornerhouserealty.com"),
    )
    runner.place_headshot = lambda _file_id, _url: None

    result = runner.run(submission)

    assert result.status == "delivered"


def test_a_found_headshot_slot_that_fails_to_change_blocks_delivery(
    db: sqlite3.Connection,
) -> None:
    """A known sample face must not survive beside another agent's name."""
    submission = _submission(rid="rid-headshot-failed")
    _record(db, submission)
    runner = _runner(db, Recorder())
    db.execute(
        "UPDATE salespeople SET headshot_url = ? WHERE email = ?",
        ("http://example.invalid/lolo.jpg", "lolo@cornerhouserealty.com"),
    )
    runner.place_headshot = lambda _file_id, _url: False

    result = runner.run(submission)

    assert result.status == "needs_review"
    assert any("sample headshot" in message for message in result.said)


def test_a_preflight_warning_pauses_before_copy_and_run_anyway_resumes(
    db: sqlite3.Connection,
) -> None:
    """A measured warning is a real choice, and an explicit answer is honored."""
    from gable.slides.preflight import Issue, Report

    submission = _submission(rid="rid-preflight-warning")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.preflight_template = lambda *_args: Report(
        issues=(
            Issue(
                "tight_agent_email",
                "The agent email needs more room. Run anyway or update the template?",
                advisory="I sized the agent email down to fit.",
            ),
        )
    )

    paused = runner.run(submission)
    assert paused.status == "needs_template"
    assert rec.copied is False

    runner.allow_template_warnings = True
    resumed = runner.resume(submission, paused.run_id)
    assert resumed.status == "delivered"
    assert resumed.output_url
    assert any("sized the agent email down" in message for message in resumed.said)


def test_a_structural_preflight_problem_cannot_be_overridden(db: sqlite3.Connection) -> None:
    from gable.slides.preflight import Issue, Report

    submission = _submission(rid="rid-preflight-blocker")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.allow_template_warnings = True
    runner.preflight_template = lambda *_args: Report(
        issues=(Issue("no_frame", "I could not identify the photo frame.", blocking=True),)
    )

    result = runner.run(submission)
    assert result.status == "needs_template"
    assert rec.copied is False


def test_an_unresolved_proactive_template_audit_blocks_before_preflight_or_copy(
    db: sqlite3.Connection,
) -> None:
    """A source Gable told Carmen to fix must not be used by a listing anyway."""
    submission = _submission(rid="rid-audit-blocked")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    measured = False

    def preflight(*_args: object) -> object:
        nonlocal measured
        measured = True
        raise AssertionError("an unresolved source audit must gate first")

    runner.preflight_template = preflight  # type: ignore[assignment]
    runner.template_clearance = lambda _fid, _label: (
        "I checked the new design, but its email field is too narrow. Fix it and ask me "
        "to check the template again."
    )

    result = runner.run(submission)

    assert result.status == "needs_template"
    assert measured is False
    assert rec.copied is False
    assert any("email field is too narrow" in message for message in result.said)
    from gable.db import store

    run = store.run_by_id(db, result.run_id)
    assert run is not None and run.template_file_id == "tmpl-1"


def test_an_updated_template_can_be_rechecked_before_a_photo_exists(
    db: sqlite3.Connection,
) -> None:
    """Template triage happens before the photo question, so its rerun must too."""
    from gable.slides.preflight import Issue, Report

    submission = _submission(rid="rid-template-before-photo")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = ""
    runner.preflight_template = lambda *_args: Report(
        issues=(Issue("tight_email", "The email section needs more room."),)
    )

    paused = runner.run(submission)
    assert paused.status == "needs_template"
    assert rec.copied is False

    runner.preflight_template = lambda *_args: Report()
    resumed = runner.resume(submission, paused.run_id)
    assert resumed.status == "needs_photo"
    assert rec.copied is False
    assert any("send me the image" in message.lower() for message in resumed.said)


# --- text fitting and the vision pass ---------------------------------------


def test_a_supplied_value_that_overflows_is_shrunk_before_delivery(
    db: sqlite3.Connection,
) -> None:
    """Slides cannot autofit over the API, so a long value clips silently.

    This is what shipped a price reading $510,000 as $510,00.
    """
    from gable.slides import fitting

    submission = _submission(rid="rid-fit")
    _record(db, submission)

    applied: list[dict[str, Any]] = []
    rec = Recorder()
    runner = _runner(db, rec)
    runner.read_text_boxes = lambda _fid: [
        # The address this run supplied, in a box far too narrow for it.
        fitting.TextBox(
            "p1_address",
            "7940 Oakwood Rd, Glen Burnie, MD 21061",
            52.9,
            187.5 * fitting.EMU_PER_POINT,
        )
    ]
    runner.apply = lambda _fid, reqs: applied.extend(reqs)
    runner.run(submission)

    assert applied, "an overflowing supplied value must be refitted"
    sizes = [
        float(r["updateTextStyle"]["style"]["fontSize"]["magnitude"])
        for r in applied
        if "updateTextStyle" in r
    ]
    assert sizes and sizes[0] < 52.9


def test_the_template_s_own_copy_is_never_refitted(db: sqlite3.Connection) -> None:
    """Static headline type belongs to Carmen and must survive untouched.

    Fitting every box on the slide is what shrank "Just" from 140.8pt to 89.9pt
    and "Listed" from 109.4pt to 80.7pt on the flyer reviewed 2026-08-11. No
    submission supplies those words, so no run has any business resizing them —
    and the visible result was the two words drifting apart with the address
    and price left riding high in boxes built for larger text.
    """
    from gable.slides import fitting

    submission = _submission(rid="rid-static")
    _record(db, submission)

    applied: list[dict[str, Any]] = []
    rec = Recorder()
    runner = _runner(db, rec)
    runner.read_text_boxes = lambda _fid: [
        # Headline copy that overflows on the estimator but is not this run's
        # data. It must be left exactly as the template draws it.
        fitting.TextBox("p1_just", "Just", 140.8, 20.0 * fitting.EMU_PER_POINT),
        fitting.TextBox("p1_listed", "Listed", 109.4, 20.0 * fitting.EMU_PER_POINT),
    ]
    runner.apply = lambda _fid, reqs: applied.extend(reqs)
    runner.run(submission)

    resized = [r for r in applied if "updateTextStyle" in r]
    assert resized == [], "the template's own copy must not be resized"


def test_a_box_that_already_fits_costs_no_calls(db: sqlite3.Connection) -> None:
    from gable.slides import fitting

    submission = _submission(rid="rid-nofit")
    _record(db, submission)
    applied: list[dict[str, Any]] = []
    runner = _runner(db, Recorder())
    runner.read_text_boxes = lambda _fid: [
        fitting.TextBox("p1_ok", "4", 20.0, 200 * fitting.EMU_PER_POINT)
    ]
    runner.apply = lambda _fid, reqs: applied.extend(reqs)
    runner.run(submission)
    assert applied == []


def test_a_flyer_the_vision_pass_rejects_is_not_delivered(db: sqlite3.Connection) -> None:
    """Only the vision pass can see a value that is present but clipped."""
    from gable.pipeline.vision import Inspection

    submission = _submission(rid="rid-vision")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.look_at = lambda _run_id, _image: Inspection(
        looks_right=False, confident=True, problems=["the price is cut off at the box edge"]
    )
    result = runner.run(submission)

    assert result.status == "needs_review"
    assert any("cut off" in said for said in rec.said)


def test_a_vision_check_that_could_not_run_blocks_delivery(
    db: sqlite3.Connection,
) -> None:
    """An unavailable proof cannot silently degrade into approval."""
    submission = _submission(rid="rid-novision")
    _record(db, submission)
    runner = _runner(db, Recorder())
    runner.look_at = lambda _run_id, _image: Inspection(
        looks_right=False,
        confident=False,
        checked=False,
    )
    result = runner.run(submission)
    assert result.status == "needs_review"
    assert any("could not complete the visual inspection" in message for message in result.said)


# --- the photo is not optional ----------------------------------------------


def test_no_flyer_is_delivered_without_a_hero_photo(db: sqlite3.Connection) -> None:
    """A listing flyer showing the template's own placeholder is not a draft.

    One was delivered like that and announced as ready. It should have stopped
    and asked, which is what Chase specified in the first place.
    """
    submission = _submission(rid="rid-nophoto")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = ""
    result = runner.run(submission)

    assert result.status == "needs_photo"
    assert rec.copied is False, "nothing should be built before there is a photo"

    # Two messages, not one. A single flat message starts no thread, and the
    # photo handoff only accepts an upload that arrives inside the listing's
    # thread — so a combined message leaves Carmen nowhere to put the photo.
    headline, question = rec.said[0], rec.said[1]
    assert submission.intake.address in headline
    assert rec.threads[0] is None, "the announcement is the root of the thread"
    assert rec.threads[1] == "1786.0", "the question is a reply underneath it"

    # Plain words. "Hero" is our name for the photo well, not Carmen's, and the
    # question has to be answerable without learning our vocabulary.
    assert "send me the image" in question.lower()
    assert "hero" not in question.lower()


def test_an_unusable_photo_url_stops_before_a_flyer_is_copied(
    db: sqlite3.Connection,
) -> None:
    """The live URL check is injected and a rejection pauses the run safely."""
    submission = _submission(rid="rid-bad-photo-url")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.check_photo = lambda _url, _slot: (False, "that image link did not load")

    result = runner.run(submission)

    assert result.status == "needs_photo"
    assert rec.copied is False
    assert any("did not load" in said for said in rec.said)


def test_a_photo_resumes_the_existing_run_without_opening_another(
    db: sqlite3.Connection,
) -> None:
    """A Slack upload continues the paused audit trail instead of forking it."""
    from gable.db import store

    submission = _submission(rid="rid-resume-photo")
    _record(db, submission)
    rec = Recorder()
    waiting = _runner(db, rec)
    waiting.hero_photo_url = ""
    paused = waiting.run(submission)
    assert paused.status == "needs_photo"

    resumed = _runner(db, rec).resume(submission, paused.run_id)

    assert resumed.status == "delivered"
    assert store.run_attempt_count(db, submission.response_row_id) == 1
    statuses = [
        row["status"]
        for row in db.execute(
            "SELECT status FROM run_events WHERE run_id = ? ORDER BY id", (paused.run_id,)
        ).fetchall()
    ]
    assert "needs_photo" in statuses
    assert statuses[-1] == "delivered"


def test_a_second_resume_does_not_build_a_duplicate_flyer(db: sqlite3.Connection) -> None:
    """The first paused-run claim wins even when another event has stale context."""
    submission = _submission(rid="rid-resume-once")
    _record(db, submission)
    waiting = _runner(db, Recorder())
    waiting.hero_photo_url = ""
    paused = waiting.run(submission)

    first_rec = Recorder()
    first = _runner(db, first_rec).resume(submission, paused.run_id)
    second_rec = Recorder()
    second = _runner(db, second_rec).resume(submission, paused.run_id)

    assert first.status == "delivered"
    assert second.status == "delivered"
    assert first_rec.copied is True
    assert second_rec.copied is False
    assert "another copy" in second_rec.said[-1]


def test_a_photo_that_will_not_go_on_stops_delivery(db: sqlite3.Connection) -> None:
    """Given a photo and unable to place it, stopping beats shipping without."""
    submission = _submission(rid="rid-photofail")
    _record(db, submission)

    class NoPlace(Recorder):
        def place_photo(
            self,
            _run_id: str,
            _file_id: str,
            _url: str,
            _template_label: str,
        ) -> bool:
            """Fail the way a rejected image URL would."""
            return False

    rec = NoPlace()
    result = _runner(db, rec).run(submission)
    assert result.status == "needs_review"
    assert "could not get the photo onto it" in rec.said[-1]


def test_the_photo_is_placed_on_a_delivered_flyer(db: sqlite3.Connection) -> None:
    submission = _submission(rid="rid-photook")
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)
    assert result.status == "delivered"
    assert rec.photo_placed is True


def test_an_unsafe_text_match_stops_before_photo_placement(db: sqlite3.Connection) -> None:
    class UnsafeRecorder(Recorder):
        def fill(self, file_id: str, pairs: dict[str, str]) -> int:  # noqa: ARG002
            self.filled = pairs
            return -1

    rec = UnsafeRecorder()
    submission = _submission(rid="rid-unsafe-text")
    _record(db, submission)
    result = _runner(db, rec).run(submission)

    assert result.status == "needs_review"
    assert rec.photo_placed is False
    assert "did not match exactly once" in result.said[-1]
