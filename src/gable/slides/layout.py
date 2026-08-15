"""Measuring what a built flyer does to its design's layout.

The rendered vision pass reads pixels and gives an opinion. This reads
rectangles and gives evidence, which is stronger for the two defects Chase
reported on 2026-08-14 and the visual gate did not: a band running off the page
edge, and text sitting on top of other text.

**Everything here is judged against the source design, never in isolation.**
These designs bleed deliberately — Open House's footer rule starts 408 points
off the left edge, New Listing with Open House's price tag hangs off the right —
and a portrait is *supposed* to overlap the band behind it. A check that flagged
every overlap would cry wolf on all six designs. So the question is never "does
this element cross the page edge" but "does it cross further than it did in the
design Carmen drew". Anything Gable did not change cannot be Gable's fault, and
anything Gable created is measured against the frame it replaced.

Pure geometry: two presentations in, a list of sentences out. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from gable.slides.elements import text_content

#: EMU per point, the unit every Slides transform is expressed in.
EMU_PER_POINT: Final[float] = 12700.0

#: Slack about a point. Slides stores transforms as floats and a copy can
#: differ in the last digit; a change smaller than this is not a defect, it is
#: arithmetic.
TOLERANCE_EMU: Final[float] = EMU_PER_POINT

#: Below this an overlap is a hairline where two boxes were drawn to touch.
#: Measured against the six designs: their intended abutments all sit under it,
#: and Kirby-Jay John's name sitting on his own title was far above it.
MIN_OVERLAP_POINTS: Final[float] = 6.0

#: The page edges, in the order `Box.overflow` reports them.
EDGES: Final[tuple[str, ...]] = ("left", "top", "right", "bottom")

#: Prefixes Gable gives the objects it creates, so they can be told from the
#: design's own elements without a lookup table.
CREATED_PREFIXES: Final[tuple[str, ...]] = ("gableHero_", "gableFace_")


@dataclass(frozen=True, slots=True)
class Box:
    """One leaf element's rendered rectangle, in EMU from the page's corner."""

    object_id: str
    x: float
    y: float
    width: float
    height: float
    text: str = ""

    @property
    def right(self) -> float:
        """The right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """The bottom edge."""
        return self.y + self.height

    @property
    def is_created(self) -> bool:
        """Whether Gable made this object rather than the designer."""
        return self.object_id.startswith(CREATED_PREFIXES)

    def overflow(self, page_width: float, page_height: float) -> tuple[float, ...]:
        """How far this box reaches past each page edge: left, top, right, bottom.

        Kept per side on purpose. A design that already bleeds off the bottom
        would otherwise mask a brand-new bleed off the right, because the worst
        single number did not change.
        """
        return (
            max(0.0, -self.x),
            max(0.0, -self.y),
            max(0.0, self.right - page_width),
            max(0.0, self.bottom - page_height),
        )

    def intrusion(self, other: Box) -> float:
        """How deep two boxes overlap, on the shallower axis.

        Depth rather than area: two boxes drawn to touch along a 200-point edge
        share a lot of square measure and nothing a reader would notice, while
        a name sitting six points into the title below it is the defect.
        """
        across = min(self.right, other.right) - max(self.x, other.x)
        down = min(self.bottom, other.bottom) - max(self.y, other.y)
        return min(across, down) if across > 0 and down > 0 else 0.0


def boxes(presentation: dict[str, Any]) -> list[Box]:
    """Every leaf element on the first slide, as an absolute rectangle.

    Args:
        presentation: A `presentations.get` response.

    Returns:
        One `Box` per leaf, with group transforms already composed in. Groups
        themselves are not returned; their children are, positioned as they
        render.

    Raises:
        Nothing.
    """
    pages = presentation.get("slides", [])
    if not pages:
        return []
    found: list[Box] = []
    _walk(pages[0].get("pageElements", []), 0.0, 0.0, 1.0, 1.0, found)
    return found


def _walk(
    elements: list[dict[str, Any]],
    tx: float,
    ty: float,
    sx: float,
    sy: float,
    found: list[Box],
) -> None:
    """Collect leaves, composing each group's transform into its children."""
    for element in elements:
        transform = element.get("transform", {})
        own_x = float(transform.get("translateX", 0) or 0)
        own_y = float(transform.get("translateY", 0) or 0)
        own_sx = float(transform.get("scaleX", 1) or 1)
        own_sy = float(transform.get("scaleY", 1) or 1)
        absolute_x = tx + sx * own_x
        absolute_y = ty + sy * own_y
        group = element.get("elementGroup")
        if group:
            _walk(
                group.get("children", []),
                absolute_x,
                absolute_y,
                sx * own_sx,
                sy * own_sy,
                found,
            )
            continue
        size = element.get("size", {})
        width = float(size.get("width", {}).get("magnitude", 0) or 0) * sx * own_sx
        height = float(size.get("height", {}).get("magnitude", 0) or 0) * sy * own_sy
        found.append(
            Box(
                object_id=str(element.get("objectId") or ""),
                x=absolute_x,
                y=absolute_y,
                width=width,
                height=height,
                text=" ".join(text_content(element).split()),
            )
        )


def _page_size(presentation: dict[str, Any]) -> tuple[float, float]:
    """The slide's width and height in EMU, or zeroes when unreadable."""
    page = presentation.get("pageSize", {})
    return (
        float(page.get("width", {}).get("magnitude", 0) or 0),
        float(page.get("height", {}).get("magnitude", 0) or 0),
    )


def _readable(box: Box) -> str:
    """Name a box the way a person would, preferring its own words."""
    if box.object_id.startswith("gableHero_"):
        return "the property photo"
    if box.object_id.startswith("gableFace_"):
        return "the agent photo"
    if box.text:
        words = box.text if len(box.text) <= 32 else box.text[:29].rstrip() + "..."
        return f"the {words!r} box"
    return "a shape"


def regressions(source: dict[str, Any], built: dict[str, Any]) -> list[str]:
    """What the built flyer does to the layout that its design does not.

    Args:
        source: The design as filed in Generic Templates.
        built: The finished copy.

    Returns:
        One plain sentence per defect, worst first. Empty when the built flyer
        keeps every edge and every gap the design already had.

    Raises:
        Nothing.
    """
    page_width, page_height = _page_size(built)
    if page_width <= 0 or page_height <= 0:
        return []
    built_boxes = boxes(built)
    source_boxes = {box.object_id: box for box in boxes(source)}
    found: list[tuple[float, str]] = []
    found.extend(_bleeds(built_boxes, source_boxes, page_width, page_height))
    found.extend(_collisions(built_boxes, source_boxes))
    return [sentence for _severity, sentence in sorted(found, key=lambda item: -item[0])]


#: How far a created image may sit from the frame it replaced and still count
#: as the same rectangle. Placement copies the measured frame's transform
#: exactly, so this is float noise rather than a move.
_SAME_FRAME_EMU: Final[float] = 2 * EMU_PER_POINT


def _frame_it_replaced(box: Box, source_boxes: dict[str, Box]) -> Box | None:
    """The design's own frame that this created image was drawn over.

    A created object has a new id, so it looks as though it has no prior claim
    on the layout. It has one: placement deletes the measured frame and creates
    the image at that frame's exact position and size. Sold's photo well starts
    three points off the left edge, so every flyer built on it was reported as
    pushing the photo off the page — a defect belonging to the design.

    Args:
        box: A created image.
        source_boxes: The design's own elements, by object id.

    Returns:
        The frame it stands in, or None when nothing in the design matches, in
        which case the image really did appear from nowhere.

    Raises:
        Nothing.
    """
    if not box.is_created:
        return None
    return next(
        (
            candidate
            for candidate in source_boxes.values()
            if abs(candidate.x - box.x) <= _SAME_FRAME_EMU
            and abs(candidate.y - box.y) <= _SAME_FRAME_EMU
            and abs(candidate.width - box.width) <= _SAME_FRAME_EMU
            and abs(candidate.height - box.height) <= _SAME_FRAME_EMU
        ),
        None,
    )


def _bleeds(
    built_boxes: list[Box],
    source_boxes: dict[str, Box],
    page_width: float,
    page_height: float,
) -> list[tuple[float, str]]:
    """Elements reaching further off the page than the design meant them to."""
    found: list[tuple[float, str]] = []
    for box in built_boxes:
        over = box.overflow(page_width, page_height)
        # A design's own overhang is the designer's decision. Only the amount
        # Gable added counts, and an object Gable created has no prior claim.
        source = source_boxes.get(box.object_id) or _frame_it_replaced(box, source_boxes)
        before = (
            source.overflow(page_width, page_height) if source is not None else (0.0, 0.0, 0.0, 0.0)
        )
        worst = max(
            zip(EDGES, over, before, strict=True),
            key=lambda item: item[1] - item[2],
        )
        edge, now, was = worst
        added = now - was
        if added <= TOLERANCE_EMU:
            continue
        found.append(
            (
                added,
                f"{_readable(box)} runs about {added / EMU_PER_POINT:.0f} points "
                f"past the {edge} edge of the page.",
            )
        )
    return found


def _collisions(
    built_boxes: list[Box],
    source_boxes: dict[str, Box],
) -> list[tuple[float, str]]:
    """Text sitting on other text more than it did in the design."""
    written = [box for box in built_boxes if box.text and box.width > 0 and box.height > 0]
    found: list[tuple[float, str]] = []
    for index, first in enumerate(written):
        for second in written[index + 1 :]:
            deep = first.intrusion(second)
            if deep <= 0:
                continue
            # Compare with the same pair before the fill. Two boxes the
            # designer already overlapped are not a defect; two that only meet
            # once a real name and a real title are in them are.
            was = 0.0
            before_first = source_boxes.get(first.object_id)
            before_second = source_boxes.get(second.object_id)
            if before_first is not None and before_second is not None:
                was = before_first.intrusion(before_second)
            added = deep - was
            if added <= MIN_OVERLAP_POINTS * EMU_PER_POINT:
                continue
            found.append(
                (
                    added,
                    f"{_readable(first)} and {_readable(second)} overlap by about "
                    f"{added / EMU_PER_POINT:.0f} points.",
                )
            )
    return found
