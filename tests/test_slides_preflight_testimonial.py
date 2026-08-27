"""Preflight against a design that carries no property photograph.

Client Review Post is a testimonial: a quote, the client who wrote it, and the
agent who earned it. It has exactly one image well and that well is the agent's
face. Everything here is about not treating the absence of a house as a fault --
and about still stopping when the face itself is missing.

These live apart from `test_slides_preflight` only because that file is at the
800-line ceiling; they are the same kind of test.
"""

from __future__ import annotations

from typing import Any

from gable.slides import fields, preflight
from tests.test_slides_preflight import _agent_card


def _page(*elements: dict[str, Any]) -> dict[str, Any]:
    """An 11.25x14.06in page, the shape every one of these designs is."""
    return {
        "pageSize": {
            "width": {"magnitude": 10_285_714},
            "height": {"magnitude": 12_857_142},
        },
        "slides": [{"objectId": "page-1", "pageElements": list(elements)}],
    }


def _portrait_well() -> dict[str, Any]:
    """Client Review Post's single image well, to live measurement.

    5.55x9.49in on an 11.25x14.06in page: width-over-height 0.58, 49% of the
    slide wide, upper edge in the top half. It satisfies every structural rule
    the hero search applies, and it is the agent's face.
    """
    return {
        "objectId": "p1_i90",
        "size": {
            "width": {"magnitude": 4_933_950},
            "height": {"magnitude": 8_437_500},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": 5_066_666,
            "translateY": 1_581_250,
        },
        "shape": {"shapeProperties": {"shapeBackgroundFill": {}}},
    }


def test_a_testimonial_is_not_reported_as_missing_its_photo_frame() -> None:
    """A design with no property photograph is correct, not defective.

    Before 2026-08-27 this design's one portrait well was recorded as its hero.
    Removing that claim without telling preflight would have swapped one wrong
    message for another: "I could not identify exactly one safe main-photo
    frame. Make that frame a separate, unfilled shape near the top" -- work
    Carmen cannot do, on a design that needs nothing done to it.
    """
    name, phone = _agent_card()
    presentation = _page(name, phone, _portrait_well())
    resolution = fields.resolve(["AGENT NAME", "Phone"])

    report = preflight.analyze(
        presentation,
        "Client Review Post",
        "Client Review",
        resolution,
        {
            "agent_name": "Porsher Howard",
            "agent_phone": "443-499-3839",
            "headshot": "http://x/p.jpg",
        },
    )

    assert not any(issue.code == "missing_photo_frame" for issue in report.blockers)
    # There is no hero, so nothing is measured for a crop that will never happen.
    assert report.hero_width_px == 0
    assert report.hero_height_px == 0


def test_a_testimonial_still_stops_when_the_agent_has_no_filed_headshot() -> None:
    """Its one well is the face, so an unfiled portrait ships a stranger's."""
    name, phone = _agent_card()
    presentation = _page(name, phone, _portrait_well())
    resolution = fields.resolve(["AGENT NAME", "Phone"])

    report = preflight.analyze(
        presentation,
        "Client Review Post",
        "Client Review",
        resolution,
        {"agent_name": "Porsher Howard", "agent_phone": "443-499-3839", "headshot": ""},
    )

    assert any(issue.code == "missing_headshot" for issue in report.blockers)
