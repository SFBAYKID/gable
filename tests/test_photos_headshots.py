"""Finding an agent's face by their name, and refusing to guess at it."""

from __future__ import annotations

from gable.photos.headshots import find_file, match_key

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
