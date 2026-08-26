"""Tests for the concrete Slides seams behind the otherwise pure runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.placement import (
    place_headshot,
    place_hero_photo,
    template_clearance,
)
from gable.slides.replacement import confirmed_replacement_count, safe_replacement_requests


class FakeSlides:
    """A minimal Slides resource that records placement requests."""

    def __init__(
        self,
        *,
        fail_update: bool = False,
        complete_reply: bool = True,
        include_target: bool = True,
        include_overlay: bool = False,
        include_headshot: bool = False,
        include_image_headshot: bool = False,
        include_element_above_headshot: bool = False,
        include_sold_headshot: bool = False,
    ) -> None:
        """Configure whether the batch succeeds and reports every request."""
        self.fail_update = fail_update
        self.complete_reply = complete_reply
        self.include_target = include_target
        self.include_overlay = include_overlay
        self.include_headshot = include_headshot
        self.include_image_headshot = include_image_headshot
        self.include_element_above_headshot = include_element_above_headshot
        self.include_sold_headshot = include_sold_headshot
        self.operation = ""
        self.body: dict[str, Any] = {}

    def presentations(self) -> FakeSlides:
        """Match the discovery client's resource chain."""
        return self

    def get(self, *, presentationId: str) -> FakeSlides:  # noqa: N803
        """Select the presentation read operation."""
        assert presentationId == "deck-1"
        self.operation = "get"
        return self

    def batchUpdate(self, **kwargs: object) -> FakeSlides:  # noqa: N802
        """Capture one atomic placement batch."""
        presentation_id = kwargs["presentationId"]
        body = kwargs["body"]
        assert presentation_id == "deck-1"
        assert isinstance(body, dict)
        self.operation = "update"
        self.body = body
        return self

    def execute(self) -> dict[str, Any]:
        """Return a presentation or configured update result."""
        if self.operation == "get":
            elements: list[dict[str, Any]] = [
                {
                    "objectId": "large-design-group",
                    "size": {
                        "width": {"magnitude": 9_000_000},
                        "height": {"magnitude": 10_000_000},
                    },
                    "transform": {"scaleX": 1, "scaleY": 1},
                    "elementGroup": {"children": []},
                },
                {
                    "objectId": "white-card-panel",
                    "size": {
                        "width": {"magnitude": 9_000_000},
                        "height": {"magnitude": 8_000_000},
                    },
                    "transform": {"scaleX": 1, "scaleY": 1},
                    "shape": {
                        "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {}}}}
                    },
                },
            ]
            if self.include_target:
                elements.append(
                    {
                        "objectId": "p1_i3",
                        "size": {
                            "width": {"magnitude": 8_000_000},
                            "height": {"magnitude": 6_000_000},
                        },
                        "transform": {"scaleX": 1, "scaleY": 1},
                        "shape": {"shapeProperties": {"shapeBackgroundFill": {}}},
                    }
                )
            if self.include_overlay:
                elements.append(
                    {
                        "objectId": "headline-overlay",
                        "size": {
                            "width": {"magnitude": 2_000_000},
                            "height": {"magnitude": 500_000},
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 500_000,
                            "translateY": 500_000,
                        },
                        "shape": {
                            "shapeType": "TEXT_BOX",
                            "text": {"textElements": [{"textRun": {"content": "JUST LISTED"}}]},
                        },
                    }
                )
            if self.include_image_headshot:
                elements.extend(
                    [
                        {
                            "objectId": "current-agent-name",
                            "size": {
                                "width": {"magnitude": 2_200_000},
                                "height": {"magnitude": 400_000},
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": 5_300_000,
                                "translateY": 10_100_000,
                            },
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {
                                    "textElements": [{"textRun": {"content": "Mike Kulnich"}}]
                                },
                            },
                        },
                        {
                            "objectId": "current-agent-phone",
                            "size": {
                                "width": {"magnitude": 1_800_000},
                                "height": {"magnitude": 300_000},
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": 5_300_000,
                                "translateY": 10_800_000,
                            },
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {
                                    "textElements": [{"textRun": {"content": "410.456.3564"}}]
                                },
                            },
                        },
                    ]
                )
            if self.include_headshot:
                elements.append(
                    {
                        "objectId": "headshot-frame",
                        "size": {
                            "width": {"magnitude": 1_500_000},
                            "height": {"magnitude": 1_500_000},
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 5_500_000,
                            "translateY": 10_000_000,
                        },
                        "shape": {"shapeProperties": {"shapeBackgroundFill": {}}},
                    }
                )
            if self.include_image_headshot:
                elements.append(
                    {
                        "objectId": "sample-portrait-image",
                        "size": {
                            "width": {"magnitude": 1_500_000},
                            "height": {"magnitude": 1_800_000},
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 8_000_000,
                            "translateY": 10_000_000,
                        },
                        "image": {"contentUrl": "https://slides.example/sample-agent.jpg"},
                    }
                )
            if self.include_element_above_headshot:
                elements.append(
                    {
                        "objectId": "footer-above-headshot",
                        "size": {
                            "width": {"magnitude": 2_000_000},
                            "height": {"magnitude": 500_000},
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 1_000_000,
                            "translateY": 11_500_000,
                        },
                        "shape": {
                            "shapeType": "TEXT_BOX",
                            "text": {"textElements": [{"textRun": {"content": "footer"}}]},
                        },
                    }
                )
            if self.include_sold_headshot:
                # Current Sold geometry: the address panel deliberately crosses
                # 3.16% of the portrait's upper-right corner and sits above it.
                elements.extend(
                    [
                        {
                            "objectId": "sold-headshot-frame",
                            "size": {
                                "width": {"magnitude": 2_428_628},
                                "height": {"magnitude": 3_642_941},
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": 14_689,
                                "translateY": 8_574_785,
                            },
                            "shape": {"shapeProperties": {"shapeBackgroundFill": {}}},
                        },
                        {
                            "objectId": "address-panel-above-headshot",
                            "size": {
                                "width": {"magnitude": 6_640_500},
                                "height": {"magnitude": 1_634_100},
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": 1_886_750,
                                "translateY": 7_443_575,
                            },
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {
                                    "textElements": [{"textRun": {"content": "32 S Prospect Ave"}}]
                                },
                            },
                        },
                    ]
                )
            return {
                "pageSize": {
                    "width": {"magnitude": 10_000_000},
                    "height": {"magnitude": 12_500_000},
                },
                "slides": [
                    {
                        "objectId": "page-1",
                        "pageElements": elements,
                    }
                ],
            }
        if self.fail_update:
            msg = "simulated Slides failure"
            raise RuntimeError(msg)
        requests = self.body["requests"]
        replies: list[dict[str, Any]] = [{} for _ in requests]
        if not self.complete_reply:
            replies.pop()
        return {"replies": replies}


def test_hero_photo_success_is_based_on_the_slides_reply() -> None:
    slides = FakeSlides()

    assert (
        place_hero_photo(
            slides,
            "deck-1",
            "https://images.example/house.jpg",
            "Just Listed — Bracket Placeholders (cleanest)",
        )
        is True
    )

    requests = slides.body["requests"]
    assert [next(iter(request)) for request in requests] == [
        "deleteObject",
        "createImage",
    ]
    hero_id = requests[1]["createImage"]["objectId"]
    assert hero_id.startswith("gableHero_")
    assert requests[0]["deleteObject"]["objectId"] == "p1_i3"
    # The photo takes the frame's own bounds, not the whole slide. Sizing to the
    # slide letterboxed a landscape photo inside a portrait design and painted
    # over the layout underneath it.
    assert requests[1]["createImage"]["elementProperties"]["size"] == {
        "width": {"magnitude": 8_000_000, "unit": "EMU"},
        "height": {"magnitude": 6_000_000, "unit": "EMU"},
    }
    assert requests[1]["createImage"]["elementProperties"]["transform"] == {
        "scaleX": 1,
        "scaleY": 1,
        "translateX": 0,
        "translateY": 0,
        "unit": "EMU",
    }


def test_hero_photo_preserves_the_template_frames_original_layer() -> None:
    slides = FakeSlides(include_overlay=True)

    assert place_hero_photo(
        slides,
        "deck-1",
        "https://images.example/house.jpg",
        "New Listing",
    )

    requests = slides.body["requests"]
    assert [next(iter(request)) for request in requests] == [
        "deleteObject",
        "createImage",
        "updatePageElementsZOrder",
    ]
    assert requests[2]["updatePageElementsZOrder"] == {
        "pageElementObjectIds": ["headline-overlay"],
        "operation": "BRING_TO_FRONT",
    }


def test_hero_photo_is_refitted_to_the_measured_frame_once() -> None:
    slides = FakeSlides()
    measured: list[tuple[str, int, int]] = []

    def refit(url: str, width: int, height: int) -> str:
        measured.append((url, width, height))
        return "https://images.example/fitted.jpg"

    assert place_hero_photo(
        slides,
        "deck-1",
        "https://images.example/original.jpg",
        "New Listing",
        refit=refit,
    )
    assert measured == [("https://images.example/original.jpg", 864, 648)]
    request = slides.body["requests"][1]["createImage"]
    assert request["url"] == "https://images.example/fitted.jpg"


def test_hero_photo_reports_a_slides_failure_instead_of_raising() -> None:
    assert (
        place_hero_photo(
            FakeSlides(fail_update=True),
            "deck-1",
            "https://images.example/house.jpg",
            "Just Listed — Bracket Placeholders (cleanest)",
        )
        is False
    )


def test_hero_photo_rejects_an_incomplete_api_reply() -> None:
    assert (
        place_hero_photo(
            FakeSlides(complete_reply=False),
            "deck-1",
            "https://images.example/house.jpg",
            "Just Listed — Bracket Placeholders (cleanest)",
        )
        is False
    )


def test_a_template_nobody_measured_by_hand_still_places_its_photo() -> None:
    """The frame is measured from the design, so an unlisted name is fine.

    Only three of the 45 templates ever had a hand-read object id recorded, and
    one of those three was wrong. Requiring the name to be known meant 42
    designs refused to deliver. Measuring the frame removes the lookup, so a
    template the catalogue has never seen still works.
    """
    slides = FakeSlides()

    assert place_hero_photo(slides, "deck-1", "https://images.example/house.jpg", "Unknown") is True
    assert slides.body["requests"][0]["deleteObject"]["objectId"] == "p1_i3"


def test_hero_photo_refuses_when_the_measured_layer_is_absent() -> None:
    slides = FakeSlides(include_target=False)

    assert (
        place_hero_photo(
            slides,
            "deck-1",
            "https://images.example/house.jpg",
            "Just Listed — Bracket Placeholders (cleanest)",
        )
        is False
    )
    assert slides.body == {}


def test_headshot_is_fitted_once_to_its_measured_frame_before_placement() -> None:
    slides = FakeSlides(include_headshot=True)
    measured: list[tuple[str, int, int]] = []

    def refit(url: str, width: int, height: int) -> str:
        measured.append((url, width, height))
        return "https://images.example/fitted-headshot.jpg"

    assert place_headshot(
        slides,
        "deck-1",
        "https://images.example/original-headshot.jpg",
        refit=refit,
    )

    assert measured == [("https://images.example/original-headshot.jpg", 162, 162)]
    requests = slides.body["requests"]
    assert requests[0]["deleteObject"]["objectId"] == "headshot-frame"
    assert requests[1]["createImage"]["url"] == ("https://images.example/fitted-headshot.jpg")


def test_an_existing_slides_portrait_image_is_replaced_in_place() -> None:
    slides = FakeSlides(
        include_image_headshot=True,
        include_element_above_headshot=True,
    )

    assert place_headshot(
        slides,
        "deck-1",
        "https://images.example/mike-headshot.jpg",
        {"agent_name": "Mike Kulnich", "agent_phone": "410.456.3564"},
        refit=lambda _url, _width, _height: "https://images.example/fitted-mike.jpg",
    )

    requests = slides.body["requests"]
    assert requests[0]["deleteObject"]["objectId"] == "sample-portrait-image"
    assert requests[1]["createImage"]["url"] == "https://images.example/fitted-mike.jpg"
    assert requests[2]["updatePageElementsZOrder"] == {
        "pageElementObjectIds": ["footer-above-headshot"],
        "operation": "BRING_TO_FRONT",
    }


def test_headshot_placement_fails_closed_on_image_shape_ambiguity() -> None:
    slides = FakeSlides(include_headshot=True, include_image_headshot=True)

    assert (
        place_headshot(
            slides,
            "deck-1",
            "https://images.example/mike-headshot.jpg",
            {"agent_name": "Mike Kulnich", "agent_phone": "410.456.3564"},
        )
        is False
    )
    assert slides.body == {}


def test_sold_headshot_replacement_restores_every_layer_above_the_portrait() -> None:
    """Replacing the live Sold slot keeps its overlapping address panel in front."""
    slides = FakeSlides(include_sold_headshot=True)

    assert place_headshot(
        slides,
        "deck-1",
        "https://images.example/mike-headshot.jpg",
        refit=lambda _url, _width, _height: "https://images.example/fitted-mike.jpg",
    )

    requests = slides.body["requests"]
    assert requests[0]["deleteObject"]["objectId"] == "sold-headshot-frame"
    assert requests[1]["createImage"]["url"] == "https://images.example/fitted-mike.jpg"
    assert requests[2]["updatePageElementsZOrder"] == {
        "pageElementObjectIds": ["address-panel-above-headshot"],
        "operation": "BRING_TO_FRONT",
    }


def test_listing_template_clearance_uses_the_persisted_triage_verdict(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    store.adopt_template_catalog(connection, [("baseline", "Sold", "one")])
    store.record_template_audit(
        connection,
        "new-file",
        "New Listing",
        "two",
        "needs_template",
        "The email section is too narrow. Fix it and ask me to check it again.",
    )

    assert template_clearance(connection, "baseline", "Sold") == ""
    assert "email section is too narrow" in template_clearance(
        connection, "new-file", "New Listing"
    )
    assert "not finished checking" in template_clearance(connection, "unseen-file", "Open House")
    connection.close()


def test_listing_template_clearance_refuses_a_newer_unreviewed_revision(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    store.adopt_template_catalog(connection, [("source", "Sold", "revision-one")])

    outcome = template_clearance(
        connection,
        "source",
        "Sold",
        "revision-two",
    )

    assert "changed after its last review" in outcome
    assert "older verdict" in outcome
    connection.close()


def test_replacement_is_refused_when_a_literal_matches_a_substring_twice() -> None:
    presentation = {
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    {
                        "objectId": "phone",
                        "shape": {"text": {"textElements": [{"textRun": {"content": "Phone"}}]}},
                    },
                    {
                        "objectId": "phone-label",
                        "shape": {
                            "text": {"textElements": [{"textRun": {"content": "Phone Number"}}]}
                        },
                    },
                ],
            }
        ]
    }

    assert safe_replacement_requests(presentation, {"Phone": "(555) 123-4567"}) == []


def test_replacement_counts_text_inside_imported_groups() -> None:
    presentation = {
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    {
                        "objectId": "group-1",
                        "elementGroup": {
                            "children": [
                                {
                                    "objectId": "price",
                                    "shape": {
                                        "text": {
                                            "textElements": [{"textRun": {"content": "[PRICE]"}}]
                                        }
                                    },
                                }
                            ]
                        },
                    }
                ],
            }
        ]
    }

    requests = safe_replacement_requests(presentation, {"[PRICE]": "$525,000"})

    # One field, two requests: the literal becomes a sentinel, then the sentinel
    # becomes the value, so nothing already written can be matched again.
    assert len(requests) == 2
    assert [r["replaceAllText"]["pageObjectIds"] for r in requests] == [["page-1"], ["page-1"]]
    assert requests[0]["replaceAllText"]["containsText"]["text"] == "[PRICE]"
    assert requests[1]["replaceAllText"]["replaceText"] == "$525,000"
    assert (
        requests[0]["replaceAllText"]["replaceText"]
        == (requests[1]["replaceAllText"]["containsText"]["text"])
    )


def test_repeated_standalone_fields_count_as_one_successful_request() -> None:
    # Two fields means four requests, because each field is filled in two passes.
    response = {
        "replies": [
            {"replaceAllText": {"occurrencesChanged": 2}},
            {"replaceAllText": {"occurrencesChanged": 1}},
            {"replaceAllText": {"occurrencesChanged": 2}},
            {"replaceAllText": {"occurrencesChanged": 1}},
        ]
    }

    assert confirmed_replacement_count(response, 2) == 2
    assert confirmed_replacement_count({"replies": response["replies"][:3]}, 2) == -1
    assert (
        confirmed_replacement_count(
            {
                "replies": [
                    {"replaceAllText": {"occurrencesChanged": 1}},
                    {"replaceAllText": {"occurrencesChanged": 0}},
                ]
            },
            1,
        )
        == -1
    )


def test_a_listing_builds_through_an_appearance_only_refusal(tmp_path: Path) -> None:
    """The open-house tag overhang is deliberate and must not stop a submission.

    d613511 settled this for a rebuild and left the new-listing gate refusing,
    so on 2026-08-26 Brittany Tawney's listing died on a verdict stored a week
    earlier about a tag that hangs off the page on purpose.
    """
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    store.adopt_template_catalog(connection, [("baseline", "Sold", "one")])
    store.record_template_audit(
        connection,
        "looks-odd",
        "New Listing with Open House",
        "two",
        "needs_template",
        "I inspected the New Listing with Open House design, but the open-house tag is "
        "cut off at the right edge. Fix that, then tell me to check the template again.",
        blocker_kind=store.BLOCKER_VISUAL,
    )
    store.record_template_audit(
        connection,
        "really-broken",
        "New Listing",
        "two",
        "needs_template",
        "The email section is too narrow. Fix it and ask me to check it again.",
        blocker_kind=store.BLOCKER_STRUCTURAL,
    )

    assert template_clearance(connection, "looks-odd", "New Listing with Open House") == ""
    assert "email section is too narrow" in template_clearance(
        connection, "really-broken", "New Listing"
    )
    connection.close()
