"""One function per change Carmen can ask for, each returning Slides requests.

This is the tool layer. Carmen says *"make the price bigger"* or *"that line
should be black, not red"*, the model picks the matching function here, fills its
arguments, and the orchestrator sends the result. Adding a capability later means
adding a function, not touching a pipeline — which is the whole point of the
shape.

Every function is **pure**: arguments in, JSON-serializable Slides requests out.
No network, no credentials, no presentation state. That means the entire edit
vocabulary is unit-tested without a Google account, and it means a mistaken call
is a bad request rather than a damaged file.

Verified against Google's request reference on 2026-08-10:
https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request

Two API facts that shape everything below:

* Colours are `rgbColor` floats in **0.0-1.0**, not 0-255 and not hex.
* Every `update*` request needs a `fields` mask naming exactly what changes.
  Omit it and the call is rejected; over-broad it and unlisted properties are
  reset to defaults. The masks here are deliberately narrow.

Does not handle: sending anything, discovering object ids, or deciding which
element Carmen meant. Resolving "the price" to an object id is
`slides/locate.py`'s job.
"""

from __future__ import annotations

import re
from typing import Any, Final

Request = dict[str, Any]

#: Slides measures type in points and geometry in EMU or PT. Points throughout
#: here, because that is what a human means by "font size".
MIN_FONT_PT: Final[float] = 1.0
MAX_FONT_PT: Final[float] = 400.0

#: Scale guards. A 0x scale makes an element vanish in a way that reads as data
#: loss; anything past 10x is almost certainly a misparsed instruction.
MIN_SCALE: Final[float] = 0.05
MAX_SCALE: Final[float] = 10.0

#: Text box insets, in points. Slides' own default is 7.2pt (0.1 inch) on each
#: side. "Padding" in Carmen's words means these.
DEFAULT_INSET_PT: Final[float] = 7.2
MAX_INSET_PT: Final[float] = 200.0

_HEX_RE: Final[re.Pattern[str]] = re.compile(r"^#?([0-9a-fA-F]{6})$")

#: Names Carmen is likely to say, mapped to hex. Keeps "make it black" working
#: without the model having to invent a hex code.
NAMED_COLOURS: Final[dict[str, str]] = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "navy": "#1B2A4A",
    "grey": "#808080",
    "gray": "#808080",
    "green": "#2E7D32",
    "blue": "#1565C0",
    "gold": "#B8860B",
    "cream": "#F5F0E6",
}


class EditError(Exception):
    """Raised when an edit request cannot be built safely."""


def parse_colour(value: str) -> dict[str, float]:
    """Turn `#RRGGBB` or a colour name into a Slides `rgbColor`.

    Args:
        value: `#1B2A4A`, `1B2A4A`, or a name from `NAMED_COLOURS`.

    Returns:
        `{"red": float, "green": float, "blue": float}` with each in 0.0-1.0.

    Raises:
        EditError: if the value is neither a known name nor six hex digits. The
            message lists the names, because the usual cause is Carmen saying a
            colour nobody mapped yet.
    """
    key = value.strip().lower()
    if key in NAMED_COLOURS:
        value = NAMED_COLOURS[key]
    match = _HEX_RE.match(value.strip())
    if not match:
        known = ", ".join(sorted(NAMED_COLOURS))
        msg = f"cannot read colour {value!r}; use #RRGGBB or one of: {known}"
        raise EditError(msg)
    digits = match.group(1)
    return {
        "red": int(digits[0:2], 16) / 255.0,
        "green": int(digits[2:4], 16) / 255.0,
        "blue": int(digits[4:6], 16) / 255.0,
    }


def _require_object_id(object_id: str) -> None:
    """Reject an empty target before it becomes an opaque Google error."""
    if not object_id or not object_id.strip():
        msg = "object_id is required; nothing identifies which element to change"
        raise EditError(msg)


# --- text styling -----------------------------------------------------------


def set_font_size(object_id: str, points: float) -> list[Request]:
    """Make text bigger or smaller.

    *"Make the price bigger."*

    Args:
        object_id: The shape whose text changes.
        points: New size in points.

    Returns:
        One `updateTextStyle` request covering all text in the shape.

    Raises:
        EditError: if `object_id` is empty or `points` is out of range.
    """
    _require_object_id(object_id)
    if not MIN_FONT_PT <= points <= MAX_FONT_PT:
        msg = f"font size must be {MIN_FONT_PT}-{MAX_FONT_PT}pt, got {points}"
        raise EditError(msg)
    return [
        {
            "updateTextStyle": {
                "objectId": object_id,
                "style": {"fontSize": {"magnitude": points, "unit": "PT"}},
                "textRange": {"type": "ALL"},
                "fields": "fontSize",
            }
        }
    ]


def scale_font_size(object_id: str, current_points: float, factor: float) -> list[Request]:
    """Resize text relative to what it is now. *"A bit bigger"* — not a number.

    Args:
        object_id: The shape whose text changes.
        current_points: The size it is now, read from the presentation.
        factor: Multiplier. 1.2 is a noticeable nudge; 0.8 shrinks.

    Returns:
        One `updateTextStyle` request.

    Raises:
        EditError: if the result falls outside the allowed range.
    """
    return set_font_size(object_id, round(current_points * factor, 1))


def set_font_family(object_id: str, family: str) -> list[Request]:
    """Change the typeface.

    *"Use Open Sans for that."*

    Args:
        object_id: The shape whose text changes.
        family: A font name Slides knows, e.g. `Open Sans`, `EB Garamond`.

    Returns:
        One `updateTextStyle` request.

    Raises:
        EditError: if `object_id` or `family` is empty. The font name itself is
            not validated here — Slides silently falls back on an unknown font,
            and the §4.7b vision pass is the honest check for that.
    """
    _require_object_id(object_id)
    if not family.strip():
        msg = "font family is empty"
        raise EditError(msg)
    return [
        {
            "updateTextStyle": {
                "objectId": object_id,
                "style": {"fontFamily": family.strip()},
                "textRange": {"type": "ALL"},
                "fields": "fontFamily",
            }
        }
    ]


def set_text_colour(object_id: str, colour: str) -> list[Request]:
    """Recolour text.

    *"Make the headline navy."*

    Args:
        object_id: The shape whose text changes.
        colour: Hex or a name from `NAMED_COLOURS`.

    Returns:
        One `updateTextStyle` request.

    Raises:
        EditError: on an empty target or an unreadable colour.
    """
    _require_object_id(object_id)
    return [
        {
            "updateTextStyle": {
                "objectId": object_id,
                "style": {"foregroundColor": {"opaqueColor": {"rgbColor": parse_colour(colour)}}},
                "textRange": {"type": "ALL"},
                "fields": "foregroundColor",
            }
        }
    ]


def set_text_weight(
    object_id: str, *, bold: bool | None = None, italic: bool | None = None
) -> list[Request]:
    """Bold or italicise text.

    *"Bold the address."*

    Args:
        object_id: The shape whose text changes.
        bold: Set or clear bold. `None` leaves it alone.
        italic: Set or clear italic. `None` leaves it alone.

    Returns:
        One `updateTextStyle` request, with a mask naming only what changed.

    Raises:
        EditError: if neither `bold` nor `italic` was given — an edit that
            changes nothing is a misunderstood instruction, not a no-op.
    """
    _require_object_id(object_id)
    style: dict[str, Any] = {}
    fields: list[str] = []
    if bold is not None:
        style["bold"] = bold
        fields.append("bold")
    if italic is not None:
        style["italic"] = italic
        fields.append("italic")
    if not fields:
        msg = "set_text_weight needs bold or italic; neither was given"
        raise EditError(msg)
    return [
        {
            "updateTextStyle": {
                "objectId": object_id,
                "style": style,
                "textRange": {"type": "ALL"},
                "fields": ",".join(fields),
            }
        }
    ]


# --- paragraph and spacing --------------------------------------------------


def set_alignment(object_id: str, alignment: str) -> list[Request]:
    """Align text.

    *"Centre the address."*

    Args:
        object_id: The shape whose text changes.
        alignment: `START`, `CENTER`, `END`, or `JUSTIFIED`. Case-insensitive,
            and `left`/`right` are accepted because that is what people say.

    Returns:
        One `updateParagraphStyle` request.

    Raises:
        EditError: on an unknown alignment.
    """
    _require_object_id(object_id)
    spoken = {"left": "START", "right": "END", "centre": "CENTER", "center": "CENTER"}
    value = spoken.get(alignment.strip().lower(), alignment.strip().upper())
    if value not in {"START", "CENTER", "END", "JUSTIFIED"}:
        msg = f"alignment must be left, center, right or justified; got {alignment!r}"
        raise EditError(msg)
    return [
        {
            "updateParagraphStyle": {
                "objectId": object_id,
                "style": {"alignment": value},
                "textRange": {"type": "ALL"},
                "fields": "alignment",
            }
        }
    ]


def set_line_spacing(object_id: str, percent: float) -> list[Request]:
    """Tighten or loosen the gap between lines.

    *"That text is too cramped."*

    Args:
        object_id: The shape whose text changes.
        percent: 100 is single spacing, 150 is one-and-a-half.

    Returns:
        One `updateParagraphStyle` request.

    Raises:
        EditError: if `percent` is outside 20-500.
    """
    _require_object_id(object_id)
    if not 20.0 <= percent <= 500.0:
        msg = f"line spacing must be 20-500%, got {percent}"
        raise EditError(msg)
    return [
        {
            "updateParagraphStyle": {
                "objectId": object_id,
                "style": {"lineSpacing": percent},
                "textRange": {"type": "ALL"},
                "fields": "lineSpacing",
            }
        }
    ]


def set_padding(object_id: str, **_ignored: float | None) -> list[Request]:
    """Refuse, with an explanation. Slides cannot set text insets over the API.

    *"The border padding is too wide, can you reduce it?"*

    This tool exists so the request gets a real answer instead of an opaque
    Google error. **Verified 2026-08-10 against the live API:** sending
    `leftInset` in `updateShapeProperties.shapeProperties` returns
    *"Invalid JSON payload received. Unknown name 'leftInset' ... Cannot find
    field."* `Shape.shapeProperties` exposes only `autofit`, `contentAlignment`,
    `outline`, `shadow`, `shapeBackgroundFill` and `link` — there are no inset
    fields. Insets are settable in the Slides UI but not through the API.

    What to do instead, and both are real tools here:

    * `resize_element` — a narrower or wider box changes where the text sits,
      which is what "padding" usually means in practice.
    * `set_content_alignment` — moves the text within the box vertically.

    Args:
        object_id: The shape Carmen pointed at.
        **_ignored: Accepted and discarded, so a model that calls this with
            `left=2` gets the explanation rather than a TypeError.

    Returns:
        Never returns.

    Raises:
        EditError: always, naming the two tools that do work.
    """
    _require_object_id(object_id)
    msg = (
        "Slides does not expose text insets over the API, so padding cannot be set "
        "directly (verified: the API rejects 'leftInset' as an unknown field). Use "
        "resize_element to change the box width, or set_content_alignment to move the "
        "text within it."
    )
    raise EditError(msg)


def set_content_alignment(object_id: str, alignment: str) -> list[Request]:
    """Move text vertically inside its box.

    *"Push that down a bit inside the box."*

    The nearest thing the API offers to vertical padding.

    Args:
        object_id: The shape to change.
        alignment: `TOP`, `MIDDLE` or `BOTTOM`. Case-insensitive.

    Returns:
        One `updateShapeProperties` request.

    Raises:
        EditError: on an unknown alignment.
    """
    _require_object_id(object_id)
    value = alignment.strip().upper()
    if value not in {"TOP", "MIDDLE", "BOTTOM"}:
        msg = f"content alignment must be top, middle or bottom; got {alignment!r}"
        raise EditError(msg)
    return [
        {
            "updateShapeProperties": {
                "objectId": object_id,
                "shapeProperties": {"contentAlignment": value},
                "fields": "contentAlignment",
            }
        }
    ]


def set_line_colour(object_id: str, colour: str, weight_pt: float | None = None) -> list[Request]:
    """Recolour a rule or divider.

    *"Change the line in the middle from red to black."*

    **A line is not a shape.** Verified live: `updateShapeProperties` on the
    divider in the Corner House template returns *"The object (p1_i28) is not of
    type SHAPE."* Line elements carry no `shapeType` and need
    `updateLineProperties`. Sending the wrong one fails the whole atomic batch,
    so the two cases get two tools.

    Args:
        object_id: The line element.
        colour: Hex or a name from `NAMED_COLOURS`.
        weight_pt: Optional new thickness in points.

    Returns:
        One `updateLineProperties` request.

    Raises:
        EditError: on an empty target, unreadable colour, or bad weight.
    """
    _require_object_id(object_id)
    properties: dict[str, Any] = {
        "lineFill": {"solidFill": {"color": {"rgbColor": parse_colour(colour)}}}
    }
    fields = ["lineFill.solidFill.color"]
    if weight_pt is not None:
        if not 0.0 < weight_pt <= 100.0:
            msg = f"line weight must be 0-100pt, got {weight_pt}"
            raise EditError(msg)
        properties["weight"] = {"magnitude": weight_pt, "unit": "PT"}
        fields.append("weight")
    return [
        {
            "updateLineProperties": {
                "objectId": object_id,
                "lineProperties": properties,
                "fields": ",".join(fields),
            }
        }
    ]


# --- shape appearance -------------------------------------------------------


def set_shape_fill(object_id: str, colour: str) -> list[Request]:
    """Recolour a shape's fill.

    *"Change the line in the middle from red to black."*

    A divider rule in these templates is a thin filled rectangle, so recolouring
    it is a fill change rather than an outline one.

    Args:
        object_id: The shape to recolour.
        colour: Hex or a name from `NAMED_COLOURS`.

    Returns:
        One `updateShapeProperties` request.

    Raises:
        EditError: on an empty target or unreadable colour.
    """
    _require_object_id(object_id)
    return [
        {
            "updateShapeProperties": {
                "objectId": object_id,
                "shapeProperties": {
                    "shapeBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": parse_colour(colour)}}
                    }
                },
                "fields": "shapeBackgroundFill.solidFill.color",
            }
        }
    ]


def set_outline(
    object_id: str, colour: str | None = None, weight_pt: float | None = None
) -> list[Request]:
    """Change a shape's border.

    *"Give that a thin navy border."*

    Args:
        object_id: The shape to change.
        colour: Border colour, or `None` to leave it.
        weight_pt: Border thickness in points, or `None` to leave it.

    Returns:
        One `updateShapeProperties` request.

    Raises:
        EditError: if neither argument was given, or the weight is out of range.
    """
    _require_object_id(object_id)
    outline: dict[str, Any] = {}
    fields: list[str] = []
    if colour is not None:
        outline["outlineFill"] = {"solidFill": {"color": {"rgbColor": parse_colour(colour)}}}
        fields.append("outline.outlineFill.solidFill.color")
    if weight_pt is not None:
        if not 0.0 < weight_pt <= 100.0:
            msg = f"outline weight must be 0-100pt, got {weight_pt}"
            raise EditError(msg)
        outline["weight"] = {"magnitude": weight_pt, "unit": "PT"}
        fields.append("outline.weight")
    if not fields:
        msg = "set_outline needs a colour or a weight"
        raise EditError(msg)
    return [
        {
            "updateShapeProperties": {
                "objectId": object_id,
                "shapeProperties": {"outline": outline},
                "fields": ",".join(fields),
            }
        }
    ]


def set_transparency(object_id: str, alpha: float) -> list[Request]:
    """Fade a shape.

    *"Make that panel less heavy."*

    Args:
        object_id: The shape to change.
        alpha: 1.0 fully opaque, 0.0 fully transparent.

    Returns:
        One `updateShapeProperties` request.

    Raises:
        EditError: if `alpha` is outside 0.0-1.0.
    """
    _require_object_id(object_id)
    if not 0.0 <= alpha <= 1.0:
        msg = f"alpha must be 0.0-1.0, got {alpha}"
        raise EditError(msg)
    return [
        {
            "updateShapeProperties": {
                "objectId": object_id,
                "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"alpha": alpha}}},
                "fields": "shapeBackgroundFill.solidFill.alpha",
            }
        }
    ]


# --- geometry ---------------------------------------------------------------


def scale_element(object_id: str, factor: float) -> list[Request]:
    """Make an element bigger or smaller.

    *"Make the image bigger."*

    Applies a multiplicative scale about the element's own origin. Slides
    composes this with the element's existing transform, so it is relative.

    Args:
        object_id: The element to resize.
        factor: Multiplier. 1.1 is a nudge; 0.5 halves it.

    Returns:
        One `updatePageElementTransform` request with `applyMode: RELATIVE`.

    Raises:
        EditError: if `factor` is outside `MIN_SCALE`-`MAX_SCALE`.
    """
    _require_object_id(object_id)
    if not MIN_SCALE <= factor <= MAX_SCALE:
        msg = f"scale must be {MIN_SCALE}-{MAX_SCALE}, got {factor}"
        raise EditError(msg)
    return [
        {
            "updatePageElementTransform": {
                "objectId": object_id,
                "applyMode": "RELATIVE",
                "transform": {
                    "scaleX": factor,
                    "scaleY": factor,
                    "translateX": 0,
                    "translateY": 0,
                    "unit": "PT",
                },
            }
        }
    ]


def resize_element(
    object_id: str,
    scale_x: float,
    scale_y: float,
    current_transform: dict[str, Any],
) -> list[Request]:
    """Stretch an element on one axis, without moving it.

    *"That box is too narrow for the phone number."*

    Separate from `scale_element` because widening a text box without making its
    type taller is a common fix — a box sized for the word "Phone" cannot hold
    "(443) 854-8554".

    **Why this needs the current transform.** A RELATIVE transform is composed
    with the element's existing one, and that multiplies `translateX` and
    `translateY` as well as the scale. Widening a box 4x with RELATIVE therefore
    also throws it four times further from the origin — off the slide entirely.
    This was observed live: the phone, email and website boxes vanished from a
    rendered flyer while their icons stayed put. So this emits an ABSOLUTE
    transform that keeps the original translation and changes only the scale.

    Args:
        object_id: The element to resize.
        scale_x: Horizontal multiplier, relative to the element's current scale.
        scale_y: Vertical multiplier.
        current_transform: The element's existing `transform`, straight from
            `presentations.get`. Must carry `scaleX`, `scaleY` and a `unit`.

    Returns:
        One `updatePageElementTransform` request with `applyMode: ABSOLUTE`.

    Raises:
        EditError: if either factor is out of range, or `current_transform` is
            missing the keys needed to preserve position.
    """
    _require_object_id(object_id)
    for name, value in (("scale_x", scale_x), ("scale_y", scale_y)):
        if not MIN_SCALE <= value <= MAX_SCALE:
            msg = f"{name} must be {MIN_SCALE}-{MAX_SCALE}, got {value}"
            raise EditError(msg)
    missing = {"scaleX", "scaleY", "unit"} - set(current_transform)
    if missing:
        msg = (
            f"current_transform is missing {sorted(missing)}; without it the element "
            "would be repositioned as well as resized"
        )
        raise EditError(msg)
    return [
        {
            "updatePageElementTransform": {
                "objectId": object_id,
                "applyMode": "ABSOLUTE",
                "transform": {
                    "scaleX": current_transform["scaleX"] * scale_x,
                    "scaleY": current_transform["scaleY"] * scale_y,
                    "shearX": current_transform.get("shearX", 0),
                    "shearY": current_transform.get("shearY", 0),
                    "translateX": current_transform.get("translateX", 0),
                    "translateY": current_transform.get("translateY", 0),
                    "unit": current_transform["unit"],
                },
            }
        }
    ]


def move_element(object_id: str, dx_pt: float, dy_pt: float) -> list[Request]:
    """Nudge an element.

    *"Move the price down a bit."*

    Args:
        object_id: The element to move.
        dx_pt: Points to move right; negative moves left.
        dy_pt: Points to move down; negative moves up.

    Returns:
        One `updatePageElementTransform` request with `applyMode: RELATIVE`.

    Raises:
        EditError: if both deltas are zero, or either exceeds a slide's span.
    """
    _require_object_id(object_id)
    if dx_pt == 0 and dy_pt == 0:
        msg = "move_element was asked to move nothing"
        raise EditError(msg)
    for name, value in (("dx_pt", dx_pt), ("dy_pt", dy_pt)):
        if abs(value) > 2000:
            msg = f"{name} of {value}pt is larger than any slide; likely a misread instruction"
            raise EditError(msg)
    return [
        {
            "updatePageElementTransform": {
                "objectId": object_id,
                "applyMode": "RELATIVE",
                "transform": {
                    "scaleX": 1,
                    "scaleY": 1,
                    "translateX": dx_pt,
                    "translateY": dy_pt,
                    "unit": "PT",
                },
            }
        }
    ]


def delete_element(object_id: str) -> list[Request]:
    """Remove an element entirely.

    *"Make it less busy — drop the tagline."*

    Args:
        object_id: The element to delete.

    Returns:
        One `deleteObject` request.

    Raises:
        EditError: if `object_id` is empty.

    Note:
        Destructive and not undoable through the API. The caller must confirm
        with Carmen first (AGENTS.md §2.6) — this function deliberately does not
        know how to ask.
    """
    _require_object_id(object_id)
    return [{"deleteObject": {"objectId": object_id}}]


# --- content ----------------------------------------------------------------


def replace_text(
    find: str, replace: str, page_object_ids: list[str] | None = None
) -> list[Request]:
    """Swap one literal string for another.

    *"The phone number is wrong."*

    This is how a field correction happens after render: the old value is on the
    slide as literal text, so it is matched literally rather than by token.

    Args:
        find: The exact text currently on the slide.
        replace: What it becomes. May be empty, to remove text.
        page_object_ids: Restrict to these slides. Omitting it means every slide
            in the presentation — safe on a one-slide post, not otherwise.

    Returns:
        One `replaceAllText` request.

    Raises:
        EditError: if `find` is empty, which would match unpredictably.
    """
    if not find:
        msg = "replace_text needs something to find; an empty match is unsafe"
        raise EditError(msg)
    body: dict[str, Any] = {
        "containsText": {"text": find, "matchCase": True},
        "replaceText": replace,
    }
    if page_object_ids:
        body["pageObjectIds"] = list(page_object_ids)
    return [{"replaceAllText": body}]


def replace_image(object_id: str, image_url: str) -> list[Request]:
    """Swap one image for another, keeping its frame.

    *"Use a different photo."*

    Args:
        object_id: The existing image element.
        image_url: Public URL to the replacement.

    Returns:
        One `replaceImage` request using `CENTER_CROP`, so the new photo fills
        the frame rather than letterboxing inside it.

    Raises:
        EditError: on an empty target or URL.
    """
    _require_object_id(object_id)
    if not image_url.strip():
        msg = "replace_image needs a URL"
        raise EditError(msg)
    return [
        {
            "replaceImage": {
                "imageObjectId": object_id,
                "url": image_url.strip(),
                "imageReplaceMethod": "CENTER_CROP",
            }
        }
    ]
