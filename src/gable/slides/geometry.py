"""Moving, resizing and removing elements on a slide.

Split out of `edits.py`, which had reached the 800-line ceiling, but the seam is
real rather than cosmetic: everything here manipulates a `transform`, and a
`transform` has a trap in it that the text tools do not.

**The trap.** `updatePageElementTransform` with `applyMode: RELATIVE` composes
the transform you send with the one the element already has:

    new_translateX = sent_scaleX * old_translateX + sent_translateX

So scaling RELATIVE moves the element as well as resizing it, proportionally to
how far from the page origin it already sat. This is not theoretical — widening
three contact boxes 4x on a real flyer pushed them to 16.7M, 12.5M and 20.9M EMU
on a slide 10.3M EMU wide. They vanished off the page while their icons stayed
put, and every API call returned 200.

Consequently **every resize here takes the element's current transform and emits
ABSOLUTE**, preserving translation explicitly. A tool that cannot see the
document must be handed the state it needs, or refuse — never guess.

`move_element` is the exception and is genuinely safe as RELATIVE: a pure
translation has an identity linear part, so `new_translate = old_translate +
delta` whatever the element's scale.
"""

from __future__ import annotations

from typing import Any

from gable.slides.edit_common import (
    MAX_SCALE,
    MIN_SCALE,
    EditError,
    Request,
    require_object_id,
)

#: A slide is ~10.3M EMU wide. A move larger than this is a misread instruction,
#: not a nudge.
MAX_MOVE_PT: float = 2000.0


def _require_transform(current_transform: dict[str, Any]) -> None:
    """Refuse a resize that would silently reposition the element.

    Args:
        current_transform: The element's existing transform.

    Raises:
        EditError: if the keys needed to preserve position are absent.
    """
    missing = {"scaleX", "scaleY", "unit"} - set(current_transform)
    if missing:
        msg = (
            f"current_transform is missing {sorted(missing)}; without it the element "
            "would be repositioned as well as resized"
        )
        raise EditError(msg)
    for axis in ("shearX", "shearY"):
        if current_transform.get(axis, 0):
            msg = (
                f"element has a non-zero {axis}, so it is rotated or skewed. Scaling one "
                "axis of a rotated element changes its angle; resize it in Slides instead."
            )
            raise EditError(msg)


def resize_element(
    object_id: str,
    scale_x: float,
    scale_y: float,
    current_transform: dict[str, Any],
) -> list[Request]:
    """Stretch an element on one axis, without moving it.

    *"That box is too narrow for the phone number."*

    Args:
        object_id: The element to resize.
        scale_x: Horizontal multiplier, relative to the element's current scale.
        scale_y: Vertical multiplier.
        current_transform: The element's existing `transform`, straight from
            `presentations.get`.

    Returns:
        One `updatePageElementTransform` request with `applyMode: ABSOLUTE`.

    Raises:
        EditError: if a factor is out of range, the transform is unusable, or
            the element is rotated.
    """
    require_object_id(object_id)
    for name, value in (("scale_x", scale_x), ("scale_y", scale_y)):
        if not MIN_SCALE <= value <= MAX_SCALE:
            msg = f"{name} must be {MIN_SCALE}-{MAX_SCALE}, got {value}"
            raise EditError(msg)
    _require_transform(current_transform)
    return [
        {
            "updatePageElementTransform": {
                "objectId": object_id,
                "applyMode": "ABSOLUTE",
                "transform": {
                    "scaleX": current_transform["scaleX"] * scale_x,
                    "scaleY": current_transform["scaleY"] * scale_y,
                    "shearX": 0,
                    "shearY": 0,
                    "translateX": current_transform.get("translateX", 0),
                    "translateY": current_transform.get("translateY", 0),
                    "unit": current_transform["unit"],
                },
            }
        }
    ]


def scale_element(
    object_id: str, factor: float, current_transform: dict[str, Any]
) -> list[Request]:
    """Make an element bigger or smaller, without moving it.

    *"Make the image bigger."*

    Uniform scaling, delegating to `resize_element` so there is exactly one
    implementation of the ABSOLUTE-transform rule. An earlier version sent a
    RELATIVE transform with `translateX: 0` and claimed to scale "about the
    element's own origin" — it scaled about the *page* origin, so a photo at
    1.2M EMU grew and jumped 0.65 inches sideways.

    Args:
        object_id: The element to resize.
        factor: Multiplier. 1.1 is a nudge; 0.5 halves it.
        current_transform: The element's existing `transform`.

    Returns:
        One `updatePageElementTransform` request with `applyMode: ABSOLUTE`.

    Raises:
        EditError: if `factor` is out of range or the transform is unusable.
    """
    return resize_element(object_id, factor, factor, current_transform)


def move_element(object_id: str, dx_pt: float, dy_pt: float) -> list[Request]:
    """Nudge an element.

    *"Move the price down a bit."*

    Safe as RELATIVE: a pure translation has an identity linear part, so the
    delta adds rather than compounding with the element's scale.

    Args:
        object_id: The element to move.
        dx_pt: Points to move right; negative moves left.
        dy_pt: Points to move down; negative moves up.

    Returns:
        One `updatePageElementTransform` request with `applyMode: RELATIVE`.

    Raises:
        EditError: if both deltas are zero, or either exceeds a slide's span.
    """
    require_object_id(object_id)
    if dx_pt == 0 and dy_pt == 0:
        msg = "move_element was asked to move nothing"
        raise EditError(msg)
    for name, value in (("dx_pt", dx_pt), ("dy_pt", dy_pt)):
        if abs(value) > MAX_MOVE_PT:
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
        Irreversible — Slides has no API undo, and Carmen may have hand-polished
        the file first. The caller must take a Drive copy before sending this and
        confirm with her, even though the instruction itself is unambiguous.
    """
    require_object_id(object_id)
    return [{"deleteObject": {"objectId": object_id}}]
