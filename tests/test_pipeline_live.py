"""Tests for the concrete Slides seams behind the otherwise pure runner."""

from __future__ import annotations

from typing import Any

from gable.pipeline.live import place_hero_photo, safe_replacement_requests


class FakeSlides:
    """A minimal Slides resource that records placement requests."""

    def __init__(
        self,
        *,
        fail_update: bool = False,
        complete_reply: bool = True,
        include_target: bool = True,
    ) -> None:
        """Configure whether the batch succeeds and reports every request."""
        self.fail_update = fail_update
        self.complete_reply = complete_reply
        self.include_target = include_target
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
        "updatePageElementsZOrder",
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
    assert requests[2]["updatePageElementsZOrder"]["pageElementObjectIds"] == [hero_id]


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

    assert (
        place_hero_photo(slides, "deck-1", "https://images.example/house.jpg", "Unknown") is True
    )
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

    assert len(requests) == 1
    assert requests[0]["replaceAllText"]["pageObjectIds"] == ["page-1"]
