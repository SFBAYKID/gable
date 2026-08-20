"""Certify a design once, before any listing is built against it.

Split out of `preflight.py` at the 800-line ceiling. The seam is the job, not
the arithmetic: this module answers "is this design safe to use at all", with
no listing in hand, while `preflight.analyze` answers "does THIS listing fit
THIS design" against real measured values. The two ask different questions and
report differently -- certification advises, analysis blocks.

Assumes the presentation was read whole from the Slides API. Performs no I/O.
"""

from __future__ import annotations

from typing import Any, Final

from gable.slides import fields, fitting
from gable.slides.elements import descendants, text_content
from gable.slides.measure import text_boxes

# `preflight` imports this module at the very END of its own body, so by the
# time this runs every name below is defined. Certification is a layer ON TOP of
# structural analysis -- it runs the same checks with no listing values and then
# adds the capacity estimate -- so the dependency genuinely points this way.
from gable.slides.preflight import (
    SINGLE_LINE_FIELDS,
    Issue,
    Report,
    analyze,
)

TEMPLATE_CAPACITY_CHARS: Final[dict[str, int]] = {
    "address": 52,
    "price": 14,
    "beds": 8,
    "baths": 8,
    "square_feet": 10,
    "agent_name": 28,
    "agent_phone": 18,
    "agent_email": 42,
    "client_name": 28,
    "review_quote": 280,
    "social_handle": 28,
    "neighborhood": 32,
    "website": 36,
    "open_house": 38,
}


def _average_character_capacity(
    box: fitting.TextBox,
    lines: int | None = None,
    font_size_pt: float | None = None,
) -> int:
    """Estimate average-character capacity at a selected readable type size."""
    size = box.font_size_pt if font_size_pt is None else font_size_pt
    if size <= 0 or box.width_emu <= 0:
        return 0
    available_lines = box.lines if lines is None else lines
    available = (box.width_emu / fitting.EMU_PER_POINT) * max(1, available_lines) * fitting.SAFETY
    weight = fitting.BOLD_MULTIPLIER if box.weight >= fitting.BOLD_WEIGHT else 1.0
    return max(0, int(available / (size * 0.52 * weight)))


def certify(
    presentation: dict[str, Any],
    template_label: str,
    category: str,
    *,
    slide_px: tuple[int, int] = (1080, 1350),
) -> Report:
    """Check a newly filed template before a real listing depends on it.

    Structural checks use the same evidence as listing preflight. Capacity
    checks then measure each recognised field against a documented long-but-
    normal character allowance. The estimate is described as approximate in
    Slack; the actual listing value is measured again before every build.
    """
    text = [
        text_content(element)
        for page in presentation.get("slides", [])
        for element in descendants(page.get("pageElements", []))
        if text_content(element)
    ]
    resolution = fields.resolve(text)
    structural = analyze(
        presentation,
        template_label,
        category,
        resolution,
        {},
        slide_px=slide_px,
    )
    issues = list(structural.issues)
    boxes = text_boxes(presentation)
    warned: set[str] = set()
    for field_name, expected in TEMPLATE_CAPACITY_CHARS.items():
        literals = [
            literal
            for literal in (
                resolution.fields.get(field_name, ""),
                *resolution.also.get(field_name, ()),
            )
            if literal
        ]
        for literal in literals:
            matching = [box for box in boxes if box.text.strip() == literal.strip()]
            for box in matching:
                capacity = _average_character_capacity(
                    box,
                    1 if field_name in SINGLE_LINE_FIELDS else None,
                )
                readable_capacity = _average_character_capacity(
                    box,
                    1 if field_name in SINGLE_LINE_FIELDS else None,
                    fitting.MIN_READABLE_PT + 0.1,
                )
                if capacity >= expected or readable_capacity >= expected or field_name in warned:
                    continue
                readable = field_name.replace("_", " ")
                issues.append(
                    Issue(
                        f"capacity_{field_name}",
                        (
                            f"I checked the new {template_label} design. Its {readable} "
                            f"section cannot hold the safe test of {expected} average "
                            f"characters without dropping below the "
                            f"{fitting.MIN_READABLE_PT:g}-point readability limit. Widen "
                            "that section if you can. I will still fit each real value "
                            "to it before I build."
                        ),
                        # Advisory, not a gate. This asks whether a box could
                        # hold a long-but-normal value from anyone on the
                        # roster, which is worth telling Carmen when she files a
                        # design. Every real value is measured exactly against
                        # this box before any flyer is built, so making the
                        # estimate block also stopped every listing on that
                        # design the moment she edited it.
                        blocking=False,
                    )
                )
                warned.add(field_name)
                break

    return Report(tuple(issues), structural.hero_width_px, structural.hero_height_px)
