"""Tests for the edit tools — one per change Carmen can ask for.

Each tool is a thin wrapper over one Slides request type, so these tests check
the two things that actually break in production: that the `fields` mask names
exactly what changed (too broad silently resets unlisted properties), and that
a misread instruction is refused rather than sent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gable.slides.edits import (
    NAMED_COLOURS,
    EditError,
    delete_element,
    move_element,
    occurrences_changed,
    parse_colour,
    replace_image,
    replace_text,
    resize_element,
    scale_element,
    scale_font_size,
    set_alignment,
    set_content_alignment,
    set_font_family,
    set_font_size,
    set_line_colour,
    set_line_spacing,
    set_outline,
    set_padding,
    set_shape_fill,
    set_text_colour,
    set_text_weight,
    set_transparency,
)

OID = "p1_i50"
#: A real transform shape, matching what presentations.get returns.
TRANSFORM: dict[str, Any] = {
    "scaleX": 2.0,
    "scaleY": 2.0,
    "translateX": 500.0,
    "translateY": 900.0,
    "unit": "EMU",
}


def _only(requests: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Every tool emits exactly one request; return its kind and body."""
    assert len(requests) == 1
    kind, body = next(iter(requests[0].items()))
    return kind, body


# --- colour parsing ---------------------------------------------------------


def test_hex_becomes_zero_to_one_floats() -> None:
    """Slides wants 0.0-1.0, not 0-255. Getting this wrong renders black."""
    assert parse_colour("#FF0000") == {"red": 1.0, "green": 0.0, "blue": 0.0}


def test_three_digit_hex_is_expanded() -> None:
    """A model will happily emit #ABC; expanding beats refusing."""
    assert parse_colour("#FFF") == {"red": 1.0, "green": 1.0, "blue": 1.0}
    assert parse_colour("#F00") == parse_colour("#FF0000")


def test_hex_without_hash_is_accepted() -> None:
    assert parse_colour("00FF00")["green"] == 1.0


def test_named_colours_work_because_carmen_says_black_not_hex() -> None:
    assert parse_colour("black") == {"red": 0.0, "green": 0.0, "blue": 0.0}
    assert parse_colour("BLACK") == parse_colour("black")


def test_every_named_colour_parses() -> None:
    for name in NAMED_COLOURS:
        parse_colour(name)


@pytest.mark.parametrize("bad", ["", "puce", "#GGGGGG", "12345", "#FFFF"])
def test_unreadable_colour_lists_the_known_names(bad: str) -> None:
    with pytest.raises(EditError, match="cannot read colour"):
        parse_colour(bad)


# --- font -------------------------------------------------------------------


def test_set_font_size_masks_only_font_size() -> None:
    kind, body = _only(set_font_size(OID, 32))
    assert kind == "updateTextStyle"
    assert body["fields"] == "fontSize"
    assert body["style"]["fontSize"] == {"magnitude": 32, "unit": "PT"}
    assert body["textRange"] == {"type": "ALL"}


@pytest.mark.parametrize("pt", [0, -4, 401, 1000])
def test_absurd_font_sizes_are_refused(pt: float) -> None:
    with pytest.raises(EditError, match="font size must be"):
        set_font_size(OID, pt)


def test_scale_font_size_is_relative_to_current() -> None:
    """'A bit bigger' has no number in it; the caller supplies the current size."""
    _, body = _only(scale_font_size(OID, current_points=20, factor=1.2))
    assert body["style"]["fontSize"]["magnitude"] == 24.0


def test_scaling_into_an_absurd_size_still_refuses() -> None:
    with pytest.raises(EditError):
        scale_font_size(OID, current_points=200, factor=5)


def test_set_font_family_masks_only_family() -> None:
    _, body = _only(set_font_family(OID, "  EB Garamond "))
    assert body["style"]["fontFamily"] == "EB Garamond"
    assert body["fields"] == "fontFamily"


def test_empty_font_family_is_refused() -> None:
    with pytest.raises(EditError, match="font family is empty"):
        set_font_family(OID, "   ")


def test_set_text_colour_nests_the_way_slides_expects() -> None:
    _, body = _only(set_text_colour(OID, "navy"))
    assert body["style"]["foregroundColor"]["opaqueColor"]["rgbColor"]["blue"] > 0.2
    assert body["fields"] == "foregroundColor"


def test_bold_and_italic_mask_only_what_changed() -> None:
    _, body = _only(set_text_weight(OID, bold=True))
    assert body["fields"] == "bold"
    assert "italic" not in body["style"]

    _, both = _only(set_text_weight(OID, bold=True, italic=False))
    assert set(both["fields"].split(",")) == {"bold", "italic"}


def test_a_weight_change_that_changes_nothing_is_refused() -> None:
    """A no-op edit means the instruction was misread, not that nothing was asked."""
    with pytest.raises(EditError, match="needs bold or italic"):
        set_text_weight(OID)


# --- paragraph --------------------------------------------------------------


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("left", "START"),
        ("right", "END"),
        ("center", "CENTER"),
        ("centre", "CENTER"),
        ("CENTER", "CENTER"),
        ("justified", "JUSTIFIED"),
    ],
)
def test_alignment_accepts_how_people_actually_say_it(spoken: str, expected: str) -> None:
    _, body = _only(set_alignment(OID, spoken))
    assert body["style"]["alignment"] == expected


def test_unknown_alignment_is_refused() -> None:
    with pytest.raises(EditError, match="alignment must be"):
        set_alignment(OID, "sideways")


def test_line_spacing_masks_only_spacing() -> None:
    _, body = _only(set_line_spacing(OID, 150))
    assert body["style"]["lineSpacing"] == 150
    assert body["fields"] == "lineSpacing"


@pytest.mark.parametrize("pct", [0, 10, 501])
def test_absurd_line_spacing_is_refused(pct: float) -> None:
    with pytest.raises(EditError, match="line spacing must be"):
        set_line_spacing(OID, pct)


# --- padding ----------------------------------------------------------------


def test_padding_refuses_and_says_what_to_use_instead() -> None:
    """Verified live: the API rejects `leftInset` as an unknown field.

    Slides exposes insets in its UI but not over the API, so the honest answer
    is a refusal that names the two tools that do work — not a request Google
    will reject three layers away.
    """
    with pytest.raises(EditError, match="resize_element"):
        set_padding(OID, left=20)


def test_padding_refusal_mentions_content_alignment_too() -> None:
    with pytest.raises(EditError, match="set_content_alignment"):
        set_padding(OID, top=4, bottom=4)


def test_padding_still_checks_the_target_first() -> None:
    with pytest.raises(EditError, match="object_id is required"):
        set_padding("", left=2)


@pytest.mark.parametrize("value", ["TOP", "middle", "Bottom"])
def test_content_alignment_accepts_the_three_positions(value: str) -> None:
    _, body = _only(set_content_alignment(OID, value))
    assert body["fields"] == "contentAlignment"
    assert body["shapeProperties"]["contentAlignment"] == value.upper()


def test_unknown_content_alignment_is_refused() -> None:
    with pytest.raises(EditError, match="content alignment must be"):
        set_content_alignment(OID, "sideways")


def test_line_colour_uses_update_line_properties_not_shape() -> None:
    """Verified live: a divider is a Line, and updateShapeProperties 400s on it."""
    kind, body = _only(set_line_colour(OID, "black"))
    assert kind == "updateLineProperties"
    rgb = body["lineProperties"]["lineFill"]["solidFill"]["color"]["rgbColor"]
    assert rgb == {"red": 0.0, "green": 0.0, "blue": 0.0}
    assert body["fields"] == "lineFill.solidFill.color"


def test_line_colour_can_also_set_weight() -> None:
    _, body = _only(set_line_colour(OID, "navy", weight_pt=3))
    assert set(body["fields"].split(",")) == {"lineFill.solidFill.color", "weight"}


@pytest.mark.parametrize("w", [0, -1, 101])
def test_absurd_line_weight_is_refused(w: float) -> None:
    with pytest.raises(EditError, match="line weight must be"):
        set_line_colour(OID, "black", weight_pt=w)


def test_line_colour_refuses_an_empty_target() -> None:
    with pytest.raises(EditError, match="object_id is required"):
        set_line_colour("", "black")


# --- shape appearance -------------------------------------------------------


def test_the_red_line_becomes_black() -> None:
    """Chase's example, end to end through the tool."""
    kind, body = _only(set_shape_fill(OID, "black"))
    assert kind == "updateShapeProperties"
    rgb = body["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    assert rgb == {"red": 0.0, "green": 0.0, "blue": 0.0}
    assert body["fields"] == "shapeBackgroundFill.solidFill.color"


def test_outline_can_set_colour_only() -> None:
    _, body = _only(set_outline(OID, colour="navy"))
    assert body["fields"] == "outline.outlineFill.solidFill.color"


def test_outline_can_set_weight_only() -> None:
    _, body = _only(set_outline(OID, weight_pt=2))
    assert body["fields"] == "outline.weight"


def test_outline_with_nothing_to_do_is_refused() -> None:
    with pytest.raises(EditError, match="needs a colour or a weight"):
        set_outline(OID)


def test_transparency_masks_only_alpha() -> None:
    _, body = _only(set_transparency(OID, 0.5))
    assert body["fields"] == "shapeBackgroundFill.solidFill.alpha"


@pytest.mark.parametrize("alpha", [-0.1, 1.1])
def test_alpha_outside_zero_to_one_is_refused(alpha: float) -> None:
    with pytest.raises(EditError, match="alpha must be"):
        set_transparency(OID, alpha)


# --- geometry ---------------------------------------------------------------


def test_scale_does_not_move_the_element() -> None:
    """The test this replaces asserted the bug.

    It required `applyMode: RELATIVE`, which composes with the existing
    transform and multiplies translateX/Y by the factor. A photo at 1.2M EMU
    scaled 1.5x would grow AND jump 0.65 inches sideways over the text panel.
    """
    kind, body = _only(scale_element(OID, 1.25, TRANSFORM))
    assert kind == "updatePageElementTransform"
    assert body["applyMode"] == "ABSOLUTE"
    assert body["transform"]["translateX"] == TRANSFORM["translateX"]
    assert body["transform"]["translateY"] == TRANSFORM["translateY"]


def test_scale_multiplies_the_existing_scale_uniformly() -> None:
    _, body = _only(scale_element(OID, 1.5, TRANSFORM))
    assert body["transform"]["scaleX"] == TRANSFORM["scaleX"] * 1.5
    assert body["transform"]["scaleY"] == TRANSFORM["scaleY"] * 1.5


@pytest.mark.parametrize("factor", [0, -1, 11])
def test_absurd_scale_is_refused(factor: float) -> None:
    with pytest.raises(EditError, match="must be"):
        scale_element(OID, factor, TRANSFORM)


def test_scale_refuses_a_rotated_element() -> None:
    """Scaling one axis of a rotated element changes its angle and skews it."""
    rotated = {**TRANSFORM, "shearX": 0.3}
    with pytest.raises(EditError, match="rotated or skewed"):
        scale_element(OID, 1.2, rotated)


def test_move_is_relative() -> None:
    _, body = _only(move_element(OID, 0, 18))
    assert body["applyMode"] == "RELATIVE"
    assert body["transform"]["translateY"] == 18
    assert body["transform"]["scaleX"] == 1


def test_moving_nowhere_is_refused() -> None:
    with pytest.raises(EditError, match="move nothing"):
        move_element(OID, 0, 0)


def test_moving_off_any_slide_is_refused() -> None:
    with pytest.raises(EditError, match="larger than any slide"):
        move_element(OID, 5000, 0)


def test_resize_can_widen_without_making_text_taller() -> None:
    """A box sized for the word 'Phone' cannot hold '(443) 854-8554'."""
    kind, body = _only(resize_element(OID, 3.0, 1.0, TRANSFORM))
    assert kind == "updatePageElementTransform"
    assert body["transform"]["scaleX"] == 6.0  # 2.0 existing x 3.0 asked
    assert body["transform"]["scaleY"] == 2.0  # unchanged


def test_resize_does_not_move_the_element() -> None:
    """RELATIVE scaling multiplies translation too, throwing the box off-slide.

    Observed live: widening the phone/email/website boxes 4x made them vanish
    from the rendered flyer while their icons stayed put.
    """
    _, body = _only(resize_element(OID, 4.0, 1.0, TRANSFORM))
    assert body["applyMode"] == "ABSOLUTE"
    assert body["transform"]["translateX"] == 500.0
    assert body["transform"]["translateY"] == 900.0


@pytest.mark.parametrize(("sx", "sy"), [(0, 1), (1, 0), (11, 1), (1, -2)])
def test_absurd_resize_is_refused(sx: float, sy: float) -> None:
    with pytest.raises(EditError, match="must be"):
        resize_element(OID, sx, sy, TRANSFORM)


def test_resize_refuses_an_empty_object_id() -> None:
    with pytest.raises(EditError, match="object_id is required"):
        resize_element("", 2.0, 1.0, TRANSFORM)


def test_resize_refuses_a_transform_it_cannot_preserve() -> None:
    """Without the current scale, the result would silently reposition."""
    with pytest.raises(EditError, match="would be repositioned"):
        resize_element(OID, 2.0, 1.0, {"translateX": 1})


def test_delete_element_is_a_single_delete_object() -> None:
    kind, body = _only(delete_element(OID))
    assert kind == "deleteObject"
    assert body["objectId"] == OID


# --- content ----------------------------------------------------------------


def test_replace_text_fixes_a_wrong_phone_number() -> None:
    kind, body = _only(replace_text("(443) 854-8554", "(443) 555-0100"))
    assert kind == "replaceAllText"
    assert body["containsText"]["text"] == "(443) 854-8554"
    assert body["containsText"]["matchCase"] is True
    assert body["replaceText"] == "(443) 555-0100"


def test_replace_text_can_be_scoped_to_a_page() -> None:
    _, body = _only(replace_text("[PRICE]", "$450,000", page_object_ids=["p1"]))
    assert body["pageObjectIds"] == ["p1"]


def test_replace_text_omits_scope_when_not_given() -> None:
    _, body = _only(replace_text("[PRICE]", "$450,000"))
    assert "pageObjectIds" not in body


def test_replacing_with_empty_string_is_allowed() -> None:
    """'Just delete that line of text' is a real request."""
    _, body = _only(replace_text("Website", ""))
    assert body["replaceText"] == ""


def test_an_empty_find_is_refused() -> None:
    """An empty match would hit unpredictably across the whole post."""
    with pytest.raises(EditError, match="needs something to find"):
        replace_text("", "x")


def test_replace_image_fills_rather_than_letterboxes() -> None:
    kind, body = _only(replace_image(OID, "http://x.test/a.jpg"))
    assert kind == "replaceImage"
    assert body["imageReplaceMethod"] == "CENTER_CROP"
    assert body["url"] == "http://x.test/a.jpg"


def test_replace_image_needs_a_url() -> None:
    with pytest.raises(EditError, match="needs a URL"):
        replace_image(OID, "  ")


# --- the shared guard -------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: set_font_size("", 12),
        lambda: set_font_family("", "Arial"),
        lambda: set_text_colour("", "black"),
        lambda: set_alignment("", "left"),
        lambda: set_line_spacing("", 100),
        lambda: set_padding("", top=1),
        lambda: set_shape_fill("", "black"),
        lambda: set_outline("", colour="black"),
        lambda: set_transparency("", 0.5),
        lambda: scale_element("", 1.1, TRANSFORM),
        lambda: move_element("", 1, 1),
        lambda: delete_element(""),
        lambda: replace_image("", "http://x.test/a.jpg"),
    ],
)
def test_every_targeted_tool_refuses_an_empty_object_id(
    call: Callable[[], list[dict[str, Any]]],
) -> None:
    """An empty target becomes an opaque Google error three layers away."""
    with pytest.raises(EditError, match="object_id is required"):
        call()


# --- the substring trap -----------------------------------------------------


@pytest.mark.parametrize("short", ["3", "4", "MD", "St"])
def test_a_short_literal_is_refused_because_it_matches_too_much(short: str) -> None:
    """Correcting a bed count 3 -> 4 would rewrite (443) into (444).

    replaceAllText matches substrings and answers 200 either way, so nothing
    downstream would notice the phone number had changed.
    """
    with pytest.raises(EditError, match="too short to match safely"):
        replace_text(short, "9")


def test_a_short_literal_is_allowed_once_the_caller_has_checked() -> None:
    _, body = _only(replace_text("3", "4", allow_short=True))
    assert body["replaceText"] == "4"


def test_a_whole_value_is_the_safe_way_to_correct_a_field() -> None:
    _, body = _only(replace_text("[ 4 BEDS ]", "[ 3 BEDS ]"))
    assert body["containsText"]["text"] == "[ 4 BEDS ]"


def test_occurrences_changed_reads_the_reply() -> None:
    """Nothing else read this, so Gable could not tell applied from no-op."""
    assert occurrences_changed({"replaceAllText": {"occurrencesChanged": 3}}) == 3


def test_occurrences_changed_is_zero_when_nothing_matched() -> None:
    assert occurrences_changed({"replaceAllText": {}}) == 0


def test_occurrences_changed_ignores_a_reply_of_another_kind() -> None:
    assert occurrences_changed({"createImage": {"objectId": "x"}}) == 0
