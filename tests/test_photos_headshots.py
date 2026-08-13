"""Finding an agent's face by their name, and refusing to guess at it."""

from __future__ import annotations

from typing import Any

from gable.photos.headshots import find_file, match_key
from gable.slides.hero import headshot_frames

FILES = [
    {"id": "andy", "name": "Andy Jang.jpg"},
    {"id": "kelsey", "name": "Kelsey Mahon.png"},
]


def test_the_file_named_for_the_agent_is_the_one_used() -> None:
    chosen = find_file(FILES, "Andy Jang")
    assert chosen is not None
    assert chosen["id"] == "andy"


def test_casing_and_stray_spacing_do_not_matter() -> None:
    """Carmen names these by hand."""
    assert find_file(FILES, "andy  jang") == FILES[0]
    assert find_file([{"id": "a", "name": " Andy Jang .JPG"}], "Andy Jang") is not None


def test_an_agent_with_no_file_gets_nothing_rather_than_someone_else() -> None:
    """A flyer with the design's own face is fixable. A wrong face is not."""
    assert find_file(FILES, "Herb Bryant") is None


def test_a_partial_name_never_matches() -> None:
    """No fuzzy matching: "Andy" must not resolve to Andy Jang's photo."""
    assert find_file(FILES, "Andy") is None
    assert find_file(FILES, "Jang") is None


def test_two_files_for_one_agent_are_refused() -> None:
    duplicates = [
        {"id": "one", "name": "Andy Jang.jpg"},
        {"id": "two", "name": "andy jang.png"},
    ]
    assert find_file(duplicates, "Andy Jang") is None


def test_an_empty_name_matches_nothing() -> None:
    assert find_file(FILES, "  ") is None


def test_match_key_strips_only_image_extensions() -> None:
    assert match_key("Andy Jang.jpg") == "andy jang"
    assert match_key("Andy Jang.jpeg") == "andy jang"
    assert match_key("Andy Jang.PNG") == "andy jang"
    assert match_key("Andy Jang Jr.") == "andy jang jr."


# --- the hero frame is a shape, and the photo has to match it ---------------


def test_a_wide_band_needs_a_wide_crop() -> None:
    """The failure this exists to stop, in numbers.

    The live `Sold` design's photo area is the full slide width and 37% of its
    height — an aspect of 2.14. The upload arrives fitted to the slide's own
    4:5 canvas, aspect 0.80. `createImage` fits an image inside its box instead
    of filling it, so that photo was drawn at the band's height and centred: a
    narrow column of photograph with the grey layout either side of it.
    """
    slide_w, slide_h = 10287000, 12852400
    frame_w, frame_h = 10272311, 4801718

    width_px = round(frame_w / slide_w * 1080)
    height_px = round(frame_h / slide_h * 1350)

    assert (width_px, height_px) == (1078, 504)
    assert abs(width_px / height_px - 2.139) < 0.01, "the crop matches the band, not the canvas"
    assert abs(1080 / 1350 - 0.8) < 0.01, "which is nothing like the shape that was sent"


def _shape(
    object_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    text: str = "",
) -> dict[str, Any]:
    """Build one axis-aligned Slides shape at its rendered dimensions."""
    shape: dict[str, Any] = {"shapeProperties": {"shapeBackgroundFill": {}}}
    if text:
        shape["text"] = {"textElements": [{"textRun": {"content": text}}]}
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": width},
            "height": {"magnitude": height},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": x,
            "translateY": y,
        },
        "shape": shape,
    }


def test_sold_portrait_survives_its_measured_address_panel_edge_overlap() -> None:
    """The current Sold source's 3.16% corner overlap is intentional structure."""
    portrait = _shape("sold-portrait", 14_689, 8_574_785, 2_428_628, 3_642_941)
    address = _shape(
        "address",
        1_886_750,
        7_443_575,
        6_640_500,
        1_634_100,
        text="32 S Prospect Ave Baltimore, MD 21228",
    )
    page = {"pageElements": [portrait, address]}

    frames = headshot_frames(page, 10_287_000, 12_852_400)

    assert [frame.object_id for frame in frames] == ["sold-portrait"]


def test_a_decorative_shape_substantially_covered_by_a_portrait_still_refuses_it() -> None:
    """The Sold exception cannot revive the speech-tail failure it was bounded around."""
    portrait = _shape("portrait", 1_000_000, 8_000_000, 2_400_000, 3_600_000)
    tail = _shape("speech-tail", 1_200_000, 8_200_000, 300_000, 300_000)
    page = {"pageElements": [portrait, tail]}

    assert headshot_frames(page, 10_000_000, 12_500_000) == ()
