"""The recorded photo well must not paint over what sits behind it.

Measured from the live Generic Templates designs on 2026-08-14. Under Contract
shipped a flyer with the word "Realty" sliced out of the Corner House logo: the
recorded well started 95,212 EMU above the logo's bottom edge and sits in front
of it in z-order, so filling it necessarily covered the logo's last line.
"""

from __future__ import annotations

from typing import Any

from gable.slides.hero import HERO_OBJECT_IDS, find_hero_frame

#: The real slide, in EMU.
SLIDE_WIDTH = 10287000
SLIDE_HEIGHT = 12852400

#: Under Contract's page, in its real back-to-front order, trimmed to the
#: elements that decide this: the logo, the true photo well, and the oversized
#: guide that overlaps both the logo above and the title below.
LOGO = {
    "objectId": "p1_i84",
    "shape": {"shapeProperties": {}},
    "size": {"width": {"magnitude": 3729544}, "height": {"magnitude": 1715874}},
    "transform": {"translateX": 3278728, "translateY": 170763, "scaleX": 1, "scaleY": 1},
}
TRUE_WELL = {
    "objectId": "p1_i85",
    "shape": {"shapeProperties": {}},
    "size": {"width": {"magnitude": 10268207}, "height": {"magnitude": 5337953}},
    "transform": {"translateX": 18793, "translateY": 2172432, "scaleX": 1, "scaleY": 1},
}
OVERSIZED_GUIDE = {
    "objectId": "p1_i88",
    "shape": {"shapeProperties": {}},
    "size": {"width": {"magnitude": 10270918}, "height": {"magnitude": 6781440}},
    "transform": {"translateX": 8400, "translateY": 1791425, "scaleX": 1, "scaleY": 1},
}
PAGE: dict[str, Any] = {"pageElements": [LOGO, TRUE_WELL, OVERSIZED_GUIDE]}


def _top(element: dict[str, Any]) -> float:
    """The element's top edge in EMU."""
    return float(element["transform"]["translateY"])


def _bottom(element: dict[str, Any]) -> float:
    """The element's bottom edge in EMU."""
    transform = element["transform"]
    height = float(element["size"]["height"]["magnitude"])
    return _top(element) + height * float(transform["scaleY"])


def test_under_contract_uses_the_well_that_clears_the_logo() -> None:
    """The defect that shipped: the well began above the logo's bottom edge."""
    frame = find_hero_frame(PAGE, SLIDE_WIDTH, SLIDE_HEIGHT, "Under Contract")

    assert frame is not None
    assert frame.object_id == "p1_i85"
    assert frame.y >= _bottom(LOGO), "a well starting above the logo paints over it"


def test_the_oversized_guide_is_the_one_that_would_have_clipped_the_logo() -> None:
    """States the defect directly, so the fix cannot be reverted by accident."""
    assert _top(OVERSIZED_GUIDE) < _bottom(LOGO)
    assert _bottom(LOGO) - _top(OVERSIZED_GUIDE) == 95212


def test_the_name_is_matched_regardless_of_spacing_and_case() -> None:
    frame = find_hero_frame(PAGE, SLIDE_WIDTH, SLIDE_HEIGHT, "  under   CONTRACT ")

    assert frame is not None
    assert frame.object_id == "p1_i85"


def test_every_live_design_still_has_a_recorded_well() -> None:
    """The six designs currently in Generic Templates, by their exact names."""
    assert set(HERO_OBJECT_IDS) == {
        "sold",
        "under contract",
        "open house",
        "new listing",
        "new listing with open house",
        "client review post",
    }


def test_an_unknown_design_falls_through_rather_than_guessing() -> None:
    """Two candidates and no recorded id must refuse, not pick the bigger one."""
    assert find_hero_frame(PAGE, SLIDE_WIDTH, SLIDE_HEIGHT, "Price Reduction") is None
