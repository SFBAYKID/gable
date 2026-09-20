"""Measure a Slides presentation's text boxes as they actually render.

Geometry only: sizes, scales and inherited type. Nothing here decides whether a
value fits or a design is fit to build — `slides.preflight` owns those verdicts
and reads its measurements from here.

Assumes a presentation dict exactly as `presentations.get` returns it. Does not
handle rotated or sheared boxes: those are reported as unmeasurable rather than
estimated, because an exact one-dimensional capacity is only valid on an
axis-aligned box.
"""

from __future__ import annotations

from typing import Any

from gable.slides import fitting
from gable.slides.elements import (
    font_family,
    font_size_pt,
    font_weight,
    text_content,
)


def _implied_font_size_pt(height_emu: float) -> float:
    """Estimate inherited type size from a one-line box's height."""
    if height_emu <= 0:
        return 0.0
    return max(1.0, (height_emu / fitting.EMU_PER_POINT) / 1.2)


def _axis_aligned_positive(element: dict[str, Any]) -> bool:
    """Whether exact one-dimensional text-capacity measurement is valid."""
    transform = element.get("transform", {})
    try:
        return (
            float(transform.get("scaleX", 1.0)) > 0
            and float(transform.get("scaleY", 1.0)) > 0
            and abs(float(transform.get("shearX", 0.0))) < 1e-9
            and abs(float(transform.get("shearY", 0.0))) < 1e-9
        )
    except (TypeError, ValueError):  # silent: a non-numeric magnitude is not a measurable box
        return False


def _leaves_with_group_scale(
    elements: list[dict[str, Any]],
    group_x: float = 1.0,
    group_y: float = 1.0,
) -> list[tuple[dict[str, Any], float, float, float]]:
    """Every leaf element with the scale its enclosing groups apply.

    An element's own transform shapes its box — New Listing with Open House
    stores its title as a 3,000,000 EMU square scaled to 1.11 x 0.13 — and does
    not change the size of the type inside it. Multiplying it into the type size
    read that title as 1.79pt and refused the design as unreadable.

    A group's scale does not change the rendered type either. That was assumed
    here until 2026-09-20 and measured against the renderer that day: see
    `text_boxes`. The scale is still returned, because the caller measures the
    box in absolute EMU and a reader needs to see where that came from.

    Args:
        elements: A `pageElements` list, or a group's children.
        group_x: Accumulated horizontal scale from enclosing groups.
        group_y: Accumulated vertical scale from enclosing groups.

    Returns:
        `(element, group_scale_y, width, height)` per leaf, with width and
        height already in absolute EMU and the group scale kept separate so the
        caller can apply it to the type size alone.

    Raises:
        Nothing.
    """
    out: list[tuple[dict[str, Any], float, float, float]] = []
    for element in elements:
        transform = element.get("transform", {})
        own_x = float(transform.get("scaleX", 1.0) or 1.0)
        own_y = float(transform.get("scaleY", 1.0) or 1.0)
        group = element.get("elementGroup")
        if group:
            out.extend(
                _leaves_with_group_scale(
                    group.get("children", []), group_x * own_x, group_y * own_y
                )
            )
            continue
        size = element.get("size", {})
        width = float(size.get("width", {}).get("magnitude", 0)) * own_x * group_x
        height = float(size.get("height", {}).get("magnitude", 0)) * own_y * group_y
        out.append((element, group_y, width, height))
    return out


def text_boxes(presentation: dict[str, Any]) -> list[fitting.TextBox]:
    """Measure every text box in a Slides presentation.

    Imported PowerPoint files often inherit their font size from the theme.
    Slides omits that value, so the box height supplies a conservative estimate
    instead of silently skipping the field most likely to overflow.
    """
    boxes: list[fitting.TextBox] = []
    for page in presentation.get("slides", []):
        # Absolute bounds, so a field inside grouped artwork is measured as it
        # actually renders. A child's own transform is relative to its group:
        # New Listing with Open House scales its REALTOR box to 0.75, so its
        # own numbers overstated the usable width by a third and the design was
        # refused outright rather than measured.
        #
        # The WIDTH is group-scaled; the TYPE SIZE is not. Slides renders the
        # text at the size it declares whatever the shape around it is scaled
        # to, and `declared * group_scale_y` therefore under-read every grouped
        # box by that factor. Measured against the renderer 2026-09-20 on New
        # Listing with Open House's credential box, the only grouped box any
        # run fills: 197.1pt wide, "TEAM LEADER + REALTOR" declared at 18.79pt.
        # Scaled it reads 14.09pt, needs 177.3pt, and fits — so nothing shrank
        # it, and the word REALTOR wrapped onto the phone number on a flyer
        # delivered to Carmen. Unscaled it needs 236.4pt, overflows, and is cut
        # to 15.66pt. Both predictions were confirmed by rendering the design:
        # at 18.79pt it wraps, at 15.6pt it does not.
        for element, _group_scale_y, width, height in _leaves_with_group_scale(
            page.get("pageElements", [])
        ):
            text = text_content(element)
            if not text:
                continue
            declared = font_size_pt(element)
            size_pt = declared if declared else _implied_font_size_pt(height)
            lines = 1
            if size_pt > 0 and height > 0:
                lines = max(1, int((height / fitting.EMU_PER_POINT) // (size_pt * 1.2)))
            boxes.append(
                fitting.TextBox(
                    object_id=str(element.get("objectId") or ""),
                    text=text,
                    font_size_pt=size_pt,
                    width_emu=width,
                    lines=lines,
                    weight=font_weight(element),
                    family=font_family(element),
                )
            )
    return boxes
