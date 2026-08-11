"""Read individual Slides elements, including children of imported groups.

PowerPoint imports commonly wrap a complete design in ``elementGroup``. The
Slides API then hides its text and images under ``children``; reading only the
top-level ``pageElements`` silently misses every fillable field. These helpers
give rendering, fitting, and conversational edits one shared recursive view.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def descendants(elements: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield leaf elements depth-first from a page or group.

    Args:
        elements: Slides ``pageElements`` or ``elementGroup.children``.

    Yields:
        Every non-group element, preserving document order.

    Raises:
        Nothing. Malformed groups simply yield no children.
    """
    for element in elements:
        children = element.get("elementGroup", {}).get("children", [])
        if children:
            yield from descendants(children)
        else:
            yield element


def presentation_elements(
    presentation: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``page id, leaf element`` pairs across a presentation."""
    for page in presentation.get("slides", []):
        page_id = str(page.get("objectId") or "")
        for element in descendants(page.get("pageElements", [])):
            yield page_id, element


def text_content(element: dict[str, Any]) -> str:
    """Return visible text from one shape, or an empty string."""
    runs = element.get("shape", {}).get("text", {}).get("textElements", [])
    return "".join(run.get("textRun", {}).get("content", "") for run in runs).strip()


def area(element: dict[str, Any]) -> float:
    """Return the element's scaled rectangular area in EMU squared."""
    transform = element.get("transform", {})
    size = element.get("size", {})
    width = size.get("width", {}).get("magnitude", 0) * transform.get("scaleX", 1)
    height = size.get("height", {}).get("magnitude", 0) * transform.get("scaleY", 1)
    return float(max(0, width) * max(0, height))


def font_size_pt(element: dict[str, Any]) -> float:
    """Return the first explicit text-run font size, or zero if inherited."""
    runs = element.get("shape", {}).get("text", {}).get("textElements", [])
    for run in runs:
        magnitude = run.get("textRun", {}).get("style", {}).get("fontSize", {}).get("magnitude")
        if magnitude:
            return float(magnitude)
    return 0.0
