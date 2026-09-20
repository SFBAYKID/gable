"""Measure the landscape photo row beneath a template's main photograph.

Only the three measured Carmen designs have a certified secondary row. Frames
must form an unambiguous, non-overlapping pair on one line; text, portrait wells,
painted panels, rotated artwork and groups never qualify. Geometry is re-read
on each build, so changed object ids do not imply changed coordinates.
"""

from __future__ import annotations

from typing import Any, Final

from gable.slides.elements import text_content
from gable.slides.hero import HeroFrame, _axis_aligned_positive, _element_bounds, _is_filled

ROW_DESIGNS: Final[frozenset[str]] = frozenset(
    {"new listing", "new listing with open house", "open house"}
)


def secondary_frames(
    page: dict[str, Any], width: float, height: float, hero: HeroFrame, label: str
) -> tuple[HeroFrame, ...]:
    """Return the measured left-to-right row, or raise on unsafe known layouts.

    Unknown designs have no certified secondary row. A known design must prove
    two top-level, axis-aligned landscape wells below its hero. This refuses
    ambiguous redesigns before any picture is replaced.
    """
    if " ".join(label.split()).casefold() not in ROW_DESIGNS:
        return ()
    candidates: list[HeroFrame] = []
    for element in page.get("pageElements", []):
        if ("shape" not in element and "image" not in element) or "elementGroup" in element:
            continue
        if text_content(element) or _is_filled(element) or not _axis_aligned_positive(element):
            continue
        x, y, w, h = _element_bounds(element)
        if not (w > 0 and h > 0 and 1.1 <= w / h <= 2.5):
            continue
        if not (0.2 * width <= w <= 0.49 * width and 0.08 * height <= h <= 0.25 * height):
            continue
        if y < hero.y + hero.height - 12700 or y + h > height or x < 0 or x + w > width:
            continue
        candidates.append(HeroFrame(str(element["objectId"]), x, y, w, h))
    candidates.sort(key=lambda frame: frame.x)
    if len(candidates) != 2:
        raise ValueError("the design's two smaller photo spaces could not be measured uniquely")
    left, right = candidates
    if abs(left.y - right.y) > 12700 or abs(left.height - right.height) > 12700:
        raise ValueError("the smaller photo spaces no longer form one measured row")
    if left.x + left.width >= right.x:
        raise ValueError("the smaller photo spaces overlap")
    return tuple(candidates)
