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

from gable.agents.website import OfficialProfile, ProfileLookup
from gable.db.schema import apply_migrations, connect
from gable.listings.enrich import Facts
from gable.pipeline.vision import Inspection
from gable.slides.preflight import Report
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
    assert sum(said.count(result.output_url) for said in rec.said) == 1


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


def test_contact_prerequisites_are_validated_before_the_first_slack_announcement(
    db: sqlite3.Connection,
) -> None:
    """No photo question may precede an unresolved identity or direct phone."""
    submission = _submission(rid="rid-contact-prerequisite", email="mike@example.test")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = ""
    runner.official_contact_lookup = lambda _name, _email: ProfileLookup(
        problem="the official site did not prove this contact"
    )

    result = runner.run(submission)

    assert result.status == "needs_info"
    assert len(rec.said) == 1
    assert "Can you send me the image" not in rec.said[0]
    assert rec.copied is False


def test_official_contact_fallback_reaches_values_and_then_asks_for_the_photo(
    db: sqlite3.Connection,
) -> None:
    submission = _submission(
        rid="rid-contact-official",
        email="mike@cornerhouserealty.com",
        name="Mike Kulnich",
    )
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = ""
    runner.official_contact_lookup = lambda _name, _email: ProfileLookup(
        profile=OfficialProfile(
            name="Mike Kulnich",
            email="mike@cornerhouserealty.com",
            phone="410.456.3564",
            title="REALTOR®",
            source_url="https://cornerhouserealty.com/mike-kulnich/",
        )
    )

    result = runner.run(submission)

    assert result.status == "needs_photo"
    assert "New New Listing request from Mike Kulnich" in rec.said[0]
    assert rec.said[1] == "Can you send me the image?"
    events = db.execute(
        "SELECT detail FROM run_events WHERE run_id = ? ORDER BY id", (result.run_id,)
    ).fetchall()
    assert any("phone from official_website" in row["detail"] for row in events)


def test_sold_title_is_validated_from_official_profile_before_the_photo_question(
    db: sqlite3.Connection,
) -> None:
    submission = _submission(
        rid="rid-contact-title",
        email="mike@cornerhouserealty.com",
        name="Mike Kulnich",
        request_type="Sold",
    )
    _record(db, submission)
    db.execute(
        "INSERT INTO salespeople (email, first_name, last_name, phone, template, synced_at) "
        "VALUES (?, ?, ?, ?, '', 'now')",
        ("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
    )
    rec = Recorder(slide_text=["[PROPERTY ADDRESS]", "AGENT NAME", "Phone", "Realtor"])
    runner = _runner(db, rec)
    runner.hero_photo_url = ""
    calls: list[tuple[str, str]] = []

    def lookup(name: str, email: str) -> ProfileLookup:
        calls.append((name, email))
        return ProfileLookup(
            profile=OfficialProfile(
                name=name,
                email=email,
                phone="410.456.3564",
                title="REALTOR®",
                source_url="https://cornerhouserealty.com/mike-kulnich/",
            )
        )

    runner.official_contact_lookup = lookup
    captured: dict[str, str] = {}
    runner.preflight_template = lambda _id, _label, _kind, _resolution, values: (
        captured.update(values) or Report()
    )

    result = runner.run(submission)

    assert result.status == "needs_photo"
    assert captured["agent_title"] == "REALTOR®"
    assert calls == [("Mike Kulnich", "mike@cornerhouserealty.com")]
    events = db.execute(
        "SELECT detail FROM run_events WHERE run_id = ? ORDER BY id", (result.run_id,)
    ).fetchall()
    assert any("title from official_website" in row["detail"] for row in events)


def test_researched_facts_are_cached_for_next_time(db: sqlite3.Connection) -> None:
    """The same property comes back as a listing, an open house and a sale."""
    submission = _submission()
    _record(db, submission)
    _runner(db, Recorder()).run(submission)

    from gable.db import store

    assert store.recall_facts(db, "7940 Oakwood Rd, Glen Burnie, MD 21061")["beds"] == "4"


# --- it asks rather than guessing -------------------------------------------


def test_sold_with_no_closing_price_stops_when_the_design_has_a_price_field(
    db: sqlite3.Connection,
) -> None:
    """A public list price must never masquerade as the missing sold price."""
    submission = _submission(request_type="Sold", rid="rid-sold")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = "http://example.invalid/hero.jpg"
    result = runner.run(submission)

    assert result.status == "needs_info"
    assert rec.copied is False
    assert any("price" in said.lower() for said in result.said)


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
        ({"request_type": "Sold"}, "needs_info"),
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
    rec = Recorder(
        slide_text=["[PROPERTY ADDRESS]", "AGENT NAME"],
        # Keep the fake file name aligned with the source text. The default
        # label names a manifest that requires price/bed/bath/sqft.
        template_label="Minimal Address Design",
    )
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
    runner.place_headshot = lambda _file_id, _url, _values: None

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
    runner.place_headshot = lambda _file_id, _url, _values: False

    result = runner.run(submission)

    assert result.status == "needs_review"
    assert any("sample headshot" in message for message in result.said)


def test_a_correctable_preflight_warning_builds_and_reports_one_outcome(
    db: sqlite3.Connection,
) -> None:
    """A measured, correctable layout tradeoff is Gable's work, not a question."""
    from gable.slides.preflight import Issue, Report

    submission = _submission(rid="rid-preflight-warning")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.preflight_template = lambda *_args: Report(
        issues=(
            Issue(
                "tight_agent_email",
                "The agent email was fitted to its box.",
                advisory="I sized the agent email down to fit.",
            ),
        )
    )

    result = runner.run(submission)

    assert result.status == "delivered"
    assert result.output_url
    assert rec.copied is True
    assert len(rec.said) == 1
    assert "sized the agent email down" in rec.said[0]
    assert "?" not in rec.said[0]


def test_a_structural_preflight_problem_cannot_be_overridden(db: sqlite3.Connection) -> None:
    from gable.slides.preflight import Issue, Report

    submission = _submission(rid="rid-preflight-blocker")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.preflight_template = lambda *_args: Report(
        issues=(Issue("no_frame", "I could not identify the photo frame.", blocking=True),)
    )

    result = runner.run(submission)
    assert result.status == "needs_template"
    assert rec.copied is False


def test_every_correctable_layout_warning_is_folded_into_one_outcome(
    db: sqlite3.Connection,
) -> None:
    """Text and crop adjustments never create approval loops in Slack."""
    from gable.slides.preflight import Issue, Report

    submission = _submission(rid="rid-scoped-preflight-warning")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = "http://example.invalid/hero.jpg"
    runner.preflight_template = lambda *_args: Report(
        issues=(
            Issue(
                "tight_address",
                "The address was fitted to its box.",
                advisory="I sized the address down to fit.",
            ),
            Issue(
                "large_photo_crop",
                "I center-cropped the supplied photo.",
                advisory="I center-cropped the supplied photo.",
            ),
        )
    )

    result = runner.run(submission)

    assert result.status == "delivered"
    assert rec.copied is True
    assert len(rec.said) == 1
    assert "sized the address down" in rec.said[0]
    assert "center-cropped" in rec.said[0]
    assert "?" not in rec.said[0]


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
        issues=(
            Issue(
                "unmeasured_email",
                "I could not measure the email section safely.",
                blocking=True,
            ),
        )
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


def test_a_bare_negative_vision_verdict_cannot_silently_deliver(
    db: sqlite3.Connection,
) -> None:
    """A strict schema does not require a problem sentence with a false verdict."""
    from gable.pipeline.vision import Inspection

    submission = _submission(rid="rid-vision-empty-problem")
    _record(db, submission)
    runner = _runner(db, Recorder())
    runner.look_at = lambda _run_id, _image: Inspection(looks_right=False, confident=True)

    result = runner.run(submission)

    assert result.status == "needs_review"
    assert any("looks off" in message for message in result.said)


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
