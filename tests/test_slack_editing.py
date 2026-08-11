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


def test_font_size_edit_resolves_price_and_waits_for_google_confirmation(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())
    decision = Decision(
        reply="Making the price bigger.",
        tool="set_font_size",
        arguments={"target": "price", "points": 32},
    )

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert said == "Done. I changed the price text to 32 points."
    assert slides.requests[0]["updateTextStyle"]["objectId"] == "price-shape"
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

    assert "exactly one price element" in said
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

    assert said == "Done. I resized the hero photo."
    request = slides.requests[0]["updatePageElementTransform"]
    assert request["objectId"] == "gableHero_abc123"
    assert request["applyMode"] == "ABSOLUTE"
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

    assert said == "Done. I corrected that field."
    assert slides.requests[0]["replaceAllText"]["containsText"]["text"] == "$525,000"
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

    assert said == "Done. I moved the hero photo."
    request = slides.requests[0]["updatePageElementTransform"]
    assert request["objectId"] == "gableHero_abc123"
    assert request["applyMode"] == "RELATIVE"
    assert request["transform"]["translateX"] == 4
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
    assert "did not confirm" in said
    connection.close()


def test_status_uses_the_thread_run_without_opening_slides(tmp_path: Path) -> None:
    connection = _database(tmp_path / "gable.db")
    slides = FakeSlides(_presentation())
    decision = Decision(reply="Let me check.", tool="report_status")

    said = SlideEditor(connection, slides).execute(decision, THREAD)

    assert said == "This flyer is built and linked in this thread."
    assert slides.operation == ""
    connection.close()
