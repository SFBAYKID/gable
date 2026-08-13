"""Choosing the hero photo well on a converted PowerPoint design."""

from __future__ import annotations

from typing import Any

from gable.slides.hero import HERO_OBJECT_IDS, find_hero_frame

#: The live Sold page, measured 2026-08-13.
SLIDE_WIDTH: float = 10_287_000.0
SLIDE_HEIGHT: float = 12_852_400.0


def _well(object_id: str, x: float, y: float, width: float, height: float) -> dict[str, Any]:
    """An unfilled, untexted shape: what a PPTX photo well imports as.

    Args:
        object_id: The Slides object id.
        x: Absolute left edge in EMU.
        y: Absolute top edge in EMU.
        width: Rendered width in EMU.
        height: Rendered height in EMU.

    Returns:
        A `pageElements` entry shaped like the real import.

    Raises:
        Nothing.
    """
    return {
        "objectId": object_id,
        "size": {"width": {"magnitude": width}, "height": {"magnitude": height}},
        "transform": {"scaleX": 1.0, "scaleY": 1.0, "translateX": x, "translateY": y},
        "shape": {"shapeProperties": {"shapeBackgroundFill": {}}},
    }


def test_recorded_hero_id_resolves_the_two_candidate_pptx_import() -> None:
    """The converted designs offer two photo-band candidates; the search refuses.

    Reproduces the Sold import: the real photo well plus the white panel behind
    the Corner House logo, which is not disposable. Deleting it washed the logo
    out over the brickwork, so the design is fixed by naming the well rather
    than by removing the other shape.
    """
    page = {
        "pageElements": [
            _well("p1_i87", -41_000, 0, 10_277_000, 6_092_000),
            _well("p1_i100", -2_315_000, -1_709_000, 8_230_000, 4_280_000),
        ]
    }

    assert find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT) is None

    frame = find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "Sold")
    assert frame is not None
    assert frame.object_id == "p1_i87"


def test_recorded_hero_id_matches_regardless_of_case_and_spacing() -> None:
    """The picker's own folding, so a renamed file still resolves."""
    page = {"pageElements": [_well("p1_i87", 0, 0, 10_277_000, 6_092_000)]}

    frame = find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "  sOLd  ")
    assert frame is not None
    assert frame.object_id == "p1_i87"


def test_recorded_hero_id_is_a_hint_not_an_authority() -> None:
    """A redesign that drops the named shape degrades to asking, never guessing."""
    page = {
        "pageElements": [
            _well("p1_renamed", 0, 0, 10_277_000, 6_092_000),
            _well("p1_alsohere", -2_315_000, -1_709_000, 8_230_000, 4_280_000),
        ]
    }

    assert find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "Sold") is None


def test_recorded_id_naming_an_implausible_shape_falls_back_to_the_search() -> None:
    """A named shape scaled past every sane bound is ignored, not trusted."""
    page = {
        "pageElements": [
            _well("p1_i87", 0, 0, SLIDE_WIDTH * 21, SLIDE_HEIGHT * 21),
            _well("p1_real", 0, 0, 10_277_000, 6_092_000),
        ]
    }

    frame = find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "Sold")
    assert frame is not None
    assert frame.object_id == "p1_real"


def test_an_unrecorded_template_still_uses_the_geometric_search() -> None:
    """Only the six named designs are hinted; everything else measures as before."""
    page = {"pageElements": [_well("p1_i5", 0, 0, 10_277_000, 6_092_000)]}

    frame = find_hero_frame(page, SLIDE_WIDTH, SLIDE_HEIGHT, "Some Future Design")
    assert frame is not None
    assert frame.object_id == "p1_i5"


def test_every_recorded_template_name_is_folded_for_lookup() -> None:
    """The keys must already be in the picker's folded form or they never match."""
    for key in HERO_OBJECT_IDS:
        assert key == " ".join(key.split()).casefold()
