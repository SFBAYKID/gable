"""Tests for the concrete Slides seams behind the otherwise pure runner."""

from __future__ import annotations

from typing import Any

from gable.pipeline.live import place_hero_photo


class FakeSlides:
    """A minimal Slides resource that records placement requests."""

    def __init__(self, *, fail_update: bool = False, complete_reply: bool = True) -> None:
        """Configure whether the batch succeeds and reports every request."""
        self.fail_update = fail_update
        self.complete_reply = complete_reply
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
            return {
                "pageSize": {
                    "width": {"magnitude": 10_000_000},
                    "height": {"magnitude": 12_500_000},
                },
                "slides": [
                    {
                        "objectId": "page-1",
                        "pageElements": [
                            {
                                "objectId": "small-brand-piece",
                                "size": {
                                    "width": {"magnitude": 1_000_000},
                                    "height": {"magnitude": 1_000_000},
                                },
                                "transform": {"scaleX": 1, "scaleY": 1},
                            },
                            {
                                "objectId": "photo-placeholder",
                                "size": {
                                    "width": {"magnitude": 8_000_000},
                                    "height": {"magnitude": 6_000_000},
                                },
                                "transform": {
                                    "scaleX": 1,
                                    "scaleY": 1,
                                    "translateX": 1_000_000,
                                    "translateY": 500_000,
                                },
                            },
                        ],
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

    assert place_hero_photo(slides, "deck-1", "https://images.example/house.jpg") is True

    requests = slides.body["requests"]
    assert [next(iter(request)) for request in requests] == [
        "deleteObject",
        "createImage",
        "updatePageElementsZOrder",
    ]
    hero_id = requests[1]["createImage"]["objectId"]
    assert hero_id.startswith("gableHero_")
    assert requests[2]["updatePageElementsZOrder"]["pageElementObjectIds"] == [hero_id]


def test_hero_photo_reports_a_slides_failure_instead_of_raising() -> None:
    assert (
        place_hero_photo(FakeSlides(fail_update=True), "deck-1", "https://images.example/house.jpg")
        is False
    )


def test_hero_photo_rejects_an_incomplete_api_reply() -> None:
    assert (
        place_hero_photo(
            FakeSlides(complete_reply=False), "deck-1", "https://images.example/house.jpg"
        )
        is False
    )
