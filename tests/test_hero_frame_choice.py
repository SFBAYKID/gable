"""Where the photo is drawn, versus which shape gets replaced.

Measured from the live Under Contract design on 2026-08-14, after it shipped a
flyer with the word "Realty" sliced in half. The well carrying the sample
photograph runs 95,212 EMU up behind the Corner House logo. The source gets
away with that because Slides letterboxes a picture fill whose aspect does not
match its shape, so the sample never reaches the top of its own box. Gable crops
to fill, so it did.

The design marks the real picture area with a second empty rectangle inside the
well. The well is still what gets deleted; the guide is where the image goes.
"""

from __future__ import annotations

from typing import Any

from gable.slides.designs import HERO_OBJECT_IDS, extra_deletions
from gable.slides.hero import find_hero_frame

SLIDE_WIDTH = 10287000
SLIDE_HEIGHT = 12852400


def _shape(object_id: str, x: float, y: float, width: float, height: float) -> dict[str, Any]:
    """One unfilled, untexted rectangle, as a PPTX import leaves it."""
    return {
        "objectId": object_id,
        "shape": {"shapeProperties": {}},
        "size": {"width": {"magnitude": width}, "height": {"magnitude": height}},
        "transform": {"translateX": x, "translateY": y, "scaleX": 1, "scaleY": 1},
    }


#: The logo box. Its bottom edge is the line the photo must not cross.
LOGO = _shape("p1_i84", 3278728, 170763, 3729544, 1715874)
LOGO_BOTTOM = 170763 + 1715874

#: The inner rectangle marking where the picture belongs.
GUIDE = _shape("p1_i85", 18793, 2172432, 10268207, 5337953)

#: The well: holds the sample photograph, and runs up behind the logo.
WELL = _shape("p1_i88", 8400, 1791425, 10270918, 6781440)

PAGE: dict[str, Any] = {"pageElements": [LOGO, GUIDE, WELL]}


def test_the_well_is_replaced_and_the_photo_starts_at_the_guide() -> None:
    """Both halves matter: delete the sample, start where the design says."""
    frame = find_hero_frame(PAGE, SLIDE_WIDTH, SLIDE_HEIGHT, "Under Contract")

    assert frame is not None
    assert frame.object_id == "p1_i88", "the sample photograph lives in the well"
    assert frame.y == 2172432, "the photo begins at the guide, clear of the logo"
    assert frame.y + frame.height == 1791425 + 6781440, "and still ends at the well"
    assert frame.width == 10270918, "the well's full width is kept"


def test_the_drawn_photo_clears_the_logo() -> None:
    """The defect that shipped, stated as the property that prevents it."""
    frame = find_hero_frame(PAGE, SLIDE_WIDTH, SLIDE_HEIGHT, "Under Contract")

    assert frame is not None
    assert frame.y >= LOGO_BOTTOM, "a photo starting above the logo paints over it"
    assert WELL["transform"]["translateY"] < LOGO_BOTTOM, "the well itself does run behind it"


def test_a_design_with_no_guide_keeps_the_well_exactly() -> None:
    """Five of the six live designs have no guide and must not change."""
    page: dict[str, Any] = {"pageElements": [LOGO, WELL]}

    frame = find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "Under Contract")

    assert frame is not None
    assert frame.object_id == "p1_i88"
    assert frame.y == 1791425
    assert frame.height == 6781440


def test_a_shape_that_is_not_contained_is_not_a_guide() -> None:
    """A neighbour that merely overlaps must not move the photo."""
    outside = _shape("p1_i77", 18793, 6000000, 10268207, 5337953)
    page: dict[str, Any] = {"pageElements": [LOGO, outside, WELL]}

    frame = find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "Under Contract")

    assert frame is not None
    assert frame.y == 1791425, "only a contained guide may move the photo"
    assert frame.height == 6781440


def test_a_guide_no_lower_than_its_well_is_ignored() -> None:
    """Pulling the photo down out from behind the furniture is the purpose."""
    flush = _shape("p1_i85", 18793, 1791425, 9000000, 5337953)
    page: dict[str, Any] = {"pageElements": [LOGO, flush, WELL]}

    frame = find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "Under Contract")

    assert frame is not None
    assert frame.y == 1791425


def test_a_sliver_inside_the_well_is_not_a_guide() -> None:
    """A thin rule or crop mark is not where the photograph belongs."""
    sliver = _shape("p1_i85", 18793, 2172432, 10268207, 200000)
    page: dict[str, Any] = {"pageElements": [LOGO, sliver, WELL]}

    frame = find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "Under Contract")

    assert frame is not None
    assert frame.y == 1791425


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


def test_the_photo_still_reaches_the_bottom_of_the_well() -> None:
    """Taking the guide's bounds outright left a grey gap above the title."""
    frame = find_hero_frame(PAGE, SLIDE_WIDTH, SLIDE_HEIGHT, "Under Contract")

    assert frame is not None
    guide_bottom = 2172432 + 5337953
    well_bottom = 1791425 + 6781440
    assert frame.y + frame.height > guide_bottom
    assert frame.y + frame.height == well_bottom


def test_new_listing_also_removes_its_second_photo_layer() -> None:
    """Its photograph is two shapes; replacing one left two houses stacked."""
    page: dict[str, Any] = {
        "pageElements": [
            _shape("p1_i90", 0, 144759, 10287000, 4853494),
            _shape("p1_i92", 0, 1575881, 10287000, 4721003),
        ]
    }

    assert extra_deletions(page, "New Listing", "p1_i92") == ("p1_i90",)


def test_a_design_with_one_photo_layer_deletes_nothing_extra() -> None:
    """Sold's second shape is the white panel behind the logo. Never delete it."""
    page: dict[str, Any] = {"pageElements": [LOGO, GUIDE, WELL]}

    assert extra_deletions(page, "Sold", "p1_i87") == ()
    assert extra_deletions(page, "Under Contract", "p1_i88") == ()


def test_an_absent_extra_layer_is_skipped_rather_than_requested() -> None:
    """A redesigned template must not be sent a delete for a missing shape."""
    page: dict[str, Any] = {"pageElements": [_shape("p1_i92", 0, 1575881, 10287000, 4721003)]}

    assert extra_deletions(page, "New Listing", "p1_i92") == ()


def test_the_well_is_never_returned_as_an_extra_deletion() -> None:
    page: dict[str, Any] = {"pageElements": [_shape("p1_i90", 0, 144759, 10287000, 4853494)]}

    assert extra_deletions(page, "New Listing", "p1_i90") == ()
