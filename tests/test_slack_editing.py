"""Tests for executing Slack edit decisions against a thread's actual flyer."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.slackapp.brain import Decision
from gable.slackapp.editing import SlideEditor

THREAD = "1723000000.100"


def _shape(object_id: str, text: str) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "shape": {"text": {"textElements": [{"textRun": {"content": text}}]}},
        "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"},
    }


def _presentation(*extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    _shape("price-shape", "$525,000"),
                    _shape("address-shape", "123 Main St, Baltimore, MD 21201"),
                    {
                        "objectId": "gableHero_abc123",
                        "image": {"contentUrl": "ignored"},
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 100,
                            "translateY": 200,
                            "unit": "EMU",
                        },
                    },
                    *extra,
                ],
            }
        ]
    }


class FakeSlides:
    """Records edits and returns configurable Google-style replies."""

    def __init__(self, presentation: dict[str, Any], *, complete: bool = True) -> None:
        """Bind a presentation and response completeness."""
        self.presentation = presentation
        self.complete = complete
        self.operation = ""
        self.requests: list[dict[str, Any]] = []

    def presentations(self) -> FakeSlides:
        """Match the discovery resource chain."""
        return self

    def get(self, **kwargs: object) -> FakeSlides:
        """Select presentation reading."""
        assert kwargs["presentationId"] == "deck-1"
        self.operation = "get"
        return self

    def batchUpdate(self, **kwargs: object) -> FakeSlides:  # noqa: N802
        """Capture one Slides batch."""
        assert kwargs["presentationId"] == "deck-1"
        body = kwargs["body"]
        assert isinstance(body, dict)
        requests = body["requests"]
        assert isinstance(requests, list)
        self.requests = requests
        self.operation = "update"
        return self

    def execute(self) -> dict[str, Any]:
        """Return presentation data or replies for the captured batch."""
        if self.operation == "get":
            return self.presentation
        replies: list[dict[str, Any]] = []
        for request in self.requests:
            if "replaceAllText" in request:
                replies.append({"replaceAllText": {"occurrencesChanged": 1}})
            else:
                replies.append({})
        if not self.complete and replies:
            replies.pop()
        return {"replies": replies}


def _database(path: Path) -> sqlite3.Connection:
    connection = connect(path)
    apply_migrations(connection)
    intake = Intake(
        agent_email="chase@monarchconnected.com",
        agent_name="Chase Gonzales",
        request_type="New Listing",
        address="123 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="$525,000",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )
    store.record_submission(connection, "response-1", 100, "today", intake, "hash")
    run = store.start_run(connection, "response-1")
    store.set_status(
        connection,
        run.run_id,
        "delivered",
        "test flyer",
        output_file_id="deck-1",
        output_url="https://docs.example/deck-1",
        slack_thread_ts=THREAD,
    )
    return connection


def test_font_size_edit_is_refused_before_google_mutation(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())
    decision = Decision(
        reply="Making the price bigger.",
        tool="set_font_size",
        arguments={"target": "price", "points": 32},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert "I do not edit a delivered flyer in place" in said
    assert slides.operation == ""
    assert slides.requests == []
    connection.close()


def test_confirmed_hero_replacement_pauses_for_one_upload_without_touching_old_flyer(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path / "gable.db")
    before = store.run_for_thread(connection, THREAD)
    assert before is not None
    store.set_status(
        connection,
        before.run_id,
        "delivered",
        "prior warning approvals",
    )
    connection.execute(
        "UPDATE runs SET approved_warning_codes = ?, pending_warning_code = ? WHERE run_id = ?",
        ('["large_photo_crop","tight_address"]', "large_photo_crop", before.run_id),
    )
    slides = FakeSlides(_presentation())
    decision = Decision(
        reply="Send me the new property photo.",
        tool="replace_photo",
        arguments={"which": "hero"},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    current = store.run_for_thread(connection, THREAD)
    assert current is not None
    assert said == "Send me the new property photo."
    assert current.status == "needs_photo"
    assert current.output_file_id == before.output_file_id == "deck-1"
    assert current.output_url == before.output_url
    assert current.failure_reason == "Send me the new property photo."
    # Migration-era approval columns remain append-only history. Runtime no
    # longer reads or updates them because photo fitting has no override path.
    assert current.approved_warning_codes == '["large_photo_crop","tight_address"]'
    assert current.pending_warning_code == "large_photo_crop"
    assert slides.operation == ""
    assert slides.requests == []
    # The Slack upload seam can claim this exact existing run once. A duplicate
    # event cannot start a second rebuild or consume another paid image call.
    assert store.claim_paused_run(
        connection,
        current.run_id,
        {"photo_url": "http://images.example/new-house.jpg", "photo_source": "slack_upload"},
    )
    assert not store.claim_paused_run(connection, current.run_id)
    connection.close()


def test_confirmed_headshot_replacement_waits_on_the_authoritative_drive_folder(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())
    decision = Decision(
        reply="I will use the updated filed headshot.",
        tool="replace_photo",
        arguments={"which": "headshot"},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    current = store.run_for_thread(connection, THREAD)
    assert current is not None
    assert said == (
        "Replace Chase Gonzales's image in Head Shots, then tell me to rebuild the flyer."
    )
    assert current.status == "needs_info"
    assert current.output_file_id == "deck-1"
    assert current.failure_reason == said
    assert slides.operation == ""
    connection.close()


def test_photo_replacement_is_rejected_while_the_listing_waits_on_another_problem(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path / "gable.db")
    run = store.run_for_thread(connection, THREAD)
    assert run is not None
    store.set_status(connection, run.run_id, "needs_info", "waiting for a direct phone")

    said = SlideEditor(connection, FakeSlides(_presentation())).execute(
        Decision(
            reply="Send me the new property photo.",
            tool="replace_photo",
            arguments={"which": "hero"},
        ),
        THREAD,
    )

    current = store.run_for_thread(connection, THREAD)
    assert current is not None
    assert "already waiting on something else" in said
    assert current.status == "needs_info"
    connection.close()


def test_an_ambiguous_target_is_not_ranked_or_changed(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation(_shape("second-price", "$525,000")))
    decision = Decision(
        reply="Making the price bigger.",
        tool="set_font_size",
        arguments={"target": "price", "points": 32},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert "I do not edit a delivered flyer in place" in said
    assert slides.requests == []
    connection.close()


def test_a_legacy_cached_public_fact_cannot_select_an_edit_target(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    store.remember_facts(
        connection,
        "123 Main St, Baltimore, MD 21201",
        {"beds": "9"},
        "https://example.test/wrong-property",
        0.9,
    )
    slides = FakeSlides(_presentation(_shape("unlabelled-number", "9")))
    decision = Decision(
        reply="Making the bedrooms bigger.",
        tool="set_font_size",
        arguments={"target": "bedrooms", "points": 32},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert "I do not edit a delivered flyer in place" in said
    assert slides.requests == []
    connection.close()


def test_hero_resize_targets_the_inserted_hero_object(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())
    decision = Decision(
        reply="Making the hero photo bigger.",
        tool="resize_photo",
        arguments={"which": "hero", "factor": 1.1},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert "I do not edit a delivered flyer in place" in said
    assert slides.operation == ""
    assert slides.requests == []
    connection.close()


def test_a_single_literal_field_correction_checks_occurrence_count(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())
    decision = Decision(
        reply="Changing that.",
        tool="correct_field",
        arguments={"current": "$525,000", "replacement": "$535,000"},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert "I do not edit a delivered flyer in place" in said
    assert slides.operation == ""
    assert slides.requests == []
    connection.close()


def test_move_hero_photo_executes_the_nudge_chase_asked_for(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())
    decision = Decision(
        reply="Moving the hero photo.",
        tool="move_element",
        arguments={"target": "hero photo", "dx_points": 4, "dy_points": 0},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert "I do not edit a delivered flyer in place" in said
    assert slides.operation == ""
    assert slides.requests == []
    connection.close()


def test_incomplete_google_reply_is_never_reported_as_success(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation(), complete=False)
    decision = Decision(
        reply="Making the price bigger.",
        tool="set_font_size",
        arguments={"target": "price", "points": 32},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert not said.startswith("Done")
    assert "I do not edit a delivered flyer in place" in said
    assert slides.operation == ""
    assert slides.requests == []
    connection.close()


def test_mutation_is_refused_when_post_edit_verification_is_not_connected(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())

    said = SlideEditor(connection, slides).execute(
        Decision(
            reply="Moving the hero photo.",
            tool="move_element",
            arguments={"target": "hero photo", "dx_points": 4, "dy_points": 0},
        ),
        THREAD,
    )

    assert "I do not edit a delivered flyer in place" in said
    assert slides.requests == []
    connection.close()


def test_pending_ready_outcome_blocks_a_later_edit_before_slides(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    run = store.run_for_thread(connection, THREAD)
    assert run is not None
    connection.execute(
        "UPDATE runs SET status = 'building' WHERE run_id = ?",
        (run.run_id,),
    )
    store.prepare_run_outcome(
        connection,
        run.run_id,
        "delivered",
        "Your flyer is ready. <https://docs.example/deck-1|Open the flyer>",
        pending_status="building",
        thread_ts=THREAD,
    )
    slides = FakeSlides(_presentation())

    said = SlideEditor(connection, slides).execute(
        Decision(
            reply="Moving the hero photo.",
            tool="move_element",
            arguments={"target": "hero photo", "dx_points": 4, "dy_points": 0},
        ),
        THREAD,
    )

    assert "still confirming the last outcome" in said
    assert slides.requests == []
    connection.close()


def test_status_uses_the_thread_run_without_opening_slides(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())
    decision = Decision(reply="Let me check.", tool="report_status")

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert said == "This flyer is built and linked in this thread."
    assert slides.operation == ""
    connection.close()


def test_failed_status_never_claims_the_listing_is_still_being_worked_on(tmp_path: Path) -> None:
    """A terminal failure is an outcome, not indefinite progress."""
    connection = _database(tmp_path / "gable.db")
    run = store.run_for_thread(connection, THREAD)
    assert run is not None
    store.set_status(connection, run.run_id, "failed", "fixed test failure")
    slides = FakeSlides(_presentation())

    said = SlideEditor(connection, slides).execute(
        Decision(reply="Let me check.", tool="report_status"),
        THREAD,
    )

    assert "processing failed" in said
    assert "still being worked on" not in said
    assert "did not send it as finished" in said
    assert slides.operation == ""
    connection.close()


def test_review_status_does_not_claim_visual_review_was_the_only_problem(tmp_path: Path) -> None:
    """needs_review also covers deterministic readback and placement failures."""
    connection = _database(tmp_path / "gable.db")
    run = store.run_for_thread(connection, THREAD)
    assert run is not None
    store.set_status(connection, run.run_id, "needs_review", "the hero photo could not be placed")

    said = SlideEditor(connection, FakeSlides(_presentation())).execute(
        Decision(reply="Let me check.", tool="report_status"),
        THREAD,
    )

    assert said == "This flyer is paused because its checks did not prove it is ready."
    connection.close()
