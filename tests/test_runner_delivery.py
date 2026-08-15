"""Runner delivery, photo handoff, and Slack-confirmation invariants."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gable.agents.website import OfficialProfile, ProfileLookup
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.vision import Inspection, InspectionProblemKind, InspectionRemedy
from gable.voice import is_clean
from tests.runner_support import Recorder
from tests.runner_support import record as _record
from tests.runner_support import runner as _runner
from tests.runner_support import submission as _submission


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Return a migrated runner-test database."""
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


def test_delivery_is_recorded_only_after_slack_confirms_its_message(
    db: sqlite3.Connection,
) -> None:
    """A ready Drive file is still building until its link exists in Slack.

    Two posts now: the listing announces itself while the run is still pending,
    which is what opens the thread the link then lands in.
    """
    submission = _submission(rid="rid-delivery-order")
    _record(db, submission)
    runner = _runner(db, Recorder())
    status_during_post: list[str] = []

    def say(_message: str, _thread_ts: str | None) -> str:
        current = store.latest_run(db, submission.response_row_id)
        assert current is not None
        status_during_post.append(current.status)
        return "1786468156.900001"

    runner.say = say
    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert status_during_post == ["pending", "building"]
    assert result.status == "delivered"
    assert current is not None
    assert current.status == "delivered"
    assert current.slack_thread_ts == "1786468156.900001"
    delivered_events = db.execute(
        "SELECT detail FROM run_events WHERE run_id = ? AND status = 'delivered'",
        (result.run_id,),
    ).fetchall()
    assert [event["detail"] for event in delivered_events] == [
        "Slack confirmed the delivery message"
    ]


def test_an_unconfirmed_delivery_message_never_leaves_a_delivered_run(
    db: sqlite3.Connection,
) -> None:
    """A blank Slack timestamp is not evidence that the link reached Carmen."""
    submission = _submission(rid="rid-delivery-unconfirmed")
    _record(db, submission)
    runner = _runner(db, Recorder())
    runner.say = lambda _message, _thread_ts: ""

    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert result.status == "building"
    assert current is not None
    assert current.status == "building"
    assert current.output_url.endswith("/edit")
    pending = store.pending_run_questions(db)
    assert len(pending) == 1
    assert pending[0].notification_kind == "outcome"
    assert pending[0].target_status == "delivered"
    assert not db.execute(
        "SELECT 1 FROM run_events WHERE run_id = ? AND status = 'delivered'",
        (result.run_id,),
    ).fetchone()


def test_a_slack_delivery_outage_keeps_the_verified_link_retryable(
    db: sqlite3.Connection,
) -> None:
    """Slack cannot turn a verified flyer into terminal failed work."""
    submission = _submission(rid="rid-delivery-slack-down")
    _record(db, submission)
    runner = _runner(db, Recorder())

    def unavailable(_message: str, _thread_ts: str | None) -> str:
        raise RuntimeError("test Slack outage")

    runner.say = unavailable
    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert result.status == "building"
    assert current is not None and current.status == "building"
    assert current.output_url.endswith("/edit")
    assert len(store.pending_run_questions(db)) == 1
    assert not db.execute(
        "SELECT 1 FROM run_events WHERE run_id = ? AND status = 'delivered'",
        (result.run_id,),
    ).fetchone()


def test_no_flyer_is_delivered_without_a_hero_photo(db: sqlite3.Connection) -> None:
    """A listing flyer showing the template's own placeholder is not a draft."""
    submission = _submission(rid="rid-nophoto")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = ""
    result = runner.run(submission)

    assert result.status == "needs_photo"
    assert rec.copied is False, "nothing should be built before there is a photo"
    headline, question = rec.said[0], rec.said[1]
    assert submission.intake.address in headline
    assert rec.threads[0] is None, "the announcement is the root of the thread"
    assert rec.threads[1] == "1786.0", "the question is a reply underneath it"
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


def test_pixelated_mike_render_is_held_without_exposing_the_bad_flyer(
    db: sqlite3.Connection,
) -> None:
    """The visual gate keeps a rejected Drive copy for audit, not for Slack."""
    submission = _submission(
        rid="rid-mike-pixelated",
        email="mike@cornerhouserealty.com",
        name="Mike Kulnich",
        request_type="Sold",
        address="703 Perception Way, Aberdeen, MD 21001",
        closing_price="615000",
    )
    _record(db, submission)
    db.execute(
        "INSERT INTO salespeople (email, first_name, last_name, phone, template, synced_at) "
        "VALUES (?, ?, ?, ?, '', 'now')",
        ("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
    )
    rec = Recorder(
        slide_text=["[PROPERTY ADDRESS]", "AGENT NAME", "Phone", "Realtor"],
        template_label="Sold",
    )
    runner = _runner(db, rec)
    runner.official_contact_lookup = lambda name, email, _phone: ProfileLookup(
        profile=OfficialProfile(
            name=name,
            email=email,
            phone="410.456.3564",
            title="REALTOR®",
            source_url="https://cornerhouserealty.com/mike-kulnich/",
        )
    )
    runner.look_at = lambda _run_id, _image, _expected: Inspection(
        looks_right=False,
        confident=True,
        problems=["The main property photo is badly pixelated and blurry."],
    )

    result = runner.run(submission)

    current = db.execute(
        "SELECT status, output_url FROM runs WHERE run_id = ?", (result.run_id,)
    ).fetchone()
    assert result.status == "needs_review"
    assert current["status"] == "needs_review"
    assert current["output_url"].endswith("/edit"), "retain the rejected copy for audit"
    assert result.output_url == current["output_url"]
    assert len(rec.said) == 2  # the announcement, then the outcome
    assert "badly pixelated and blurry" in rec.said[-1]
    assert "kept the supplied image and draft" in rec.said[-1]
    assert "without starting over" in rec.said[-1]
    assert "send" not in rec.said[-1].lower()
    assert "larger" not in rec.said[-1].lower()
    assert "Open it" not in rec.said[-1]
    assert current["output_url"] not in rec.said[-1]


def test_mike_wrong_property_photo_requests_one_replacement_on_the_same_run(
    db: sqlite3.Connection,
) -> None:
    """A proved photo contradiction routes directly back to thread upload."""
    submission = _submission(
        rid="rid-mike-wrong-house",
        email="mike@cornerhouserealty.com",
        name="Mike Kulnich",
        request_type="Sold",
        address="703 Perception Way, Aberdeen, MD 21001",
        closing_price="615000",
    )
    _record(db, submission)
    db.execute(
        "INSERT INTO salespeople (email, first_name, last_name, phone, template, synced_at) "
        "VALUES (?, ?, ?, ?, '', 'now')",
        ("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
    )
    rec = Recorder(
        slide_text=["[PROPERTY ADDRESS]", "AGENT NAME", "Phone", "Realtor"],
        template_label="Sold",
    )
    runner = _runner(db, rec)
    runner.official_contact_lookup = lambda name, email, _phone: ProfileLookup(
        profile=OfficialProfile(
            name=name,
            email=email,
            phone="410.456.3564",
            title="REALTOR®",
            source_url="https://cornerhouserealty.com/mike-kulnich/",
        )
    )
    runner.look_at = lambda _run_id, _image, _expected: Inspection(
        looks_right=False,
        confident=True,
        problems=["The flyer says 703, but the house number in the photo says 721."],
        remedy=InspectionRemedy.REPLACE_PHOTO,
        problem_kinds=(InspectionProblemKind.SOURCE_PHOTO_CONFLICT,),
        source_conflict_visible=True,
    )

    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert current is not None
    assert result.status == "needs_photo"
    assert current.status == "needs_photo"
    assert current.output_file_id == "out-1", "retain the rejected copy for audit"
    assert current.output_url.endswith("/edit")
    assert current.failure_reason == store.PHOTO_REPLACEMENT_WAITING
    assert store.run_attempt_count(db, submission.response_row_id) == 1
    assert len(result.said) == 1
    assert len(rec.said) == 2  # announcement, then outcome
    assert result.questions == ["Can you send the correct property image?"]
    assert "house number in the photo says 721" in rec.said[-1]
    assert "Can you send the correct property image?" in rec.said[-1]
    assert rec.said[-1].startswith("&gt;I rendered it")
    assert is_clean(rec.said[-1])
    assert "Open the flyer" not in rec.said[-1]
    assert current.output_url not in rec.said[-1]
    confirmation_events = db.execute(
        "SELECT detail FROM run_events WHERE run_id = ? AND status = 'needs_photo' ORDER BY id",
        (result.run_id,),
    ).fetchall()
    assert [str(event["detail"]) for event in confirmation_events] == [
        "replacement property photo request confirmed in Slack at 1786.0"
    ]


def test_a_conflict_visible_only_in_the_render_stays_needs_review(
    db: sqlite3.Connection,
) -> None:
    """A derivative-created house number is not evidence against the upload."""
    submission = _submission(rid="rid-render-only-number")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.look_at = lambda _run_id, _image, _expected: Inspection(
        looks_right=False,
        confident=True,
        problems=["The render shows 721, but the number is unreadable in the source image."],
        remedy=InspectionRemedy.REPLACE_PHOTO,
        problem_kinds=(InspectionProblemKind.PHOTO_OUTPUT,),
        source_conflict_visible=False,
    )

    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert current is not None
    assert result.status == current.status == "needs_review"
    assert result.questions == []
    assert "correct property image" not in rec.said[0]
    assert current.failure_reason == (
        "The render shows 721, but the number is unreadable in the source image."
    )


def test_an_unconfirmed_replacement_request_remains_durable_review(
    db: sqlite3.Connection,
) -> None:
    """A blank Slack timestamp cannot claim that a person was asked for a photo."""
    submission = _submission(rid="rid-unconfirmed-photo-request")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.look_at = lambda _run_id, _image, _expected: Inspection(
        looks_right=False,
        confident=True,
        problems=["The source house number says 721."],
        remedy=InspectionRemedy.REPLACE_PHOTO,
        problem_kinds=(InspectionProblemKind.SOURCE_PHOTO_CONFLICT,),
        source_conflict_visible=True,
    )
    attempted: list[str] = []

    def unconfirmed(message: str, _thread: str | None) -> str:
        attempted.append(message)
        return ""

    runner.say = unconfirmed

    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert current is not None
    assert result.status == current.status == "needs_review"
    assert current.failure_reason == store.QUESTION_NOTIFICATION_PENDING
    assert current.output_file_id == "out-1"
    assert current.output_url.endswith("/edit")
    assert result.said == []
    assert result.questions == []
    assert len(attempted) == 2  # the announcement, then the question
    assert "correct property image" in attempted[-1]
    assert not db.execute(
        "SELECT 1 FROM run_events WHERE run_id = ? AND status = 'needs_photo'",
        (result.run_id,),
    ).fetchone()


def test_mixed_visual_problems_stay_review_even_with_a_replace_remedy(
    db: sqlite3.Connection,
) -> None:
    """Typed non-photo evidence prevents a source replacement conversation."""
    submission = _submission(rid="rid-mixed-visual-problems")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.look_at = lambda _run_id, _image, _expected: Inspection(
        looks_right=False,
        confident=True,
        problems=["The source number says 721.", "The address overlaps the divider."],
        remedy=InspectionRemedy.REPLACE_PHOTO,
        problem_kinds=(
            InspectionProblemKind.SOURCE_PHOTO_CONFLICT,
            InspectionProblemKind.LAYOUT,
        ),
        source_conflict_visible=True,
    )

    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert current is not None
    assert result.status == current.status == "needs_review"
    assert result.questions == []
    assert "correct property image" not in rec.said[0]
    assert current.failure_reason == (
        "The source number says 721.; The address overlaps the divider."
    )


def test_replacement_question_survives_an_overlong_visual_finding(
    db: sqlite3.Connection,
) -> None:
    """The complete action stays below Slack's ceiling after a runaway sentence."""
    submission = _submission(rid="rid-long-photo-finding")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.look_at = lambda _run_id, _image, _expected: Inspection(
        looks_right=False,
        confident=True,
        problems=["The source proves a mismatch because " + ("detail " * 150)],
        remedy=InspectionRemedy.REPLACE_PHOTO,
        problem_kinds=(InspectionProblemKind.SOURCE_PHOTO_CONFLICT,),
        source_conflict_visible=True,
    )

    result = runner.run(submission)

    assert result.status == "needs_photo"
    assert len(rec.said) == 2  # the announcement, then the outcome
    assert "Can you send the correct property image?" in rec.said[-1]
    assert len(rec.said[-1]) <= 600
    assert is_clean(rec.said[-1])


# --- Gable writes in paragraphs, not one block -----------------------------


def test_a_refusal_separates_the_finding_from_what_was_done_about_it() -> None:
    """Chase, 2026-08-15: his writing needs to be not one big block."""
    from gable.pipeline import run_reporting

    said = run_reporting.mismatch("agent phone", [])

    assert said.count("\n\n") == 1
    assert said.startswith("I filled the design, but the agent phone")
    assert said.endswith("I have not sent it as finished.")


def test_every_delivery_note_is_its_own_paragraph(tmp_path: Path) -> None:
    from gable.db.schema import apply_migrations, connect
    from gable.pipeline import run_reporting

    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)

    message = run_reporting.delivery_message(
        connection,
        "run-1",
        output_url="http://example.test/edit",
        run_notes=["I looked the property up."],
        advisories=["I center-cropped the photo to the frame."],
        left_blank=[],
        price_missing_note="The closing price was not on the form.",
    )

    assert message.count("\n\n") == 3
    assert "\n\n" in message
    assert message.startswith("Your flyer is ready.")


def test_nothing_extra_leaves_a_blank_line_behind(tmp_path: Path) -> None:
    from gable.db.schema import apply_migrations, connect
    from gable.pipeline import run_reporting

    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)

    message = run_reporting.delivery_message(
        connection,
        "run-1",
        output_url="http://example.test/edit",
        run_notes=[],
        advisories=[],
        left_blank=[],
    )

    assert "\n" not in message
