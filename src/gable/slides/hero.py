"""Finding the hero photo frame in a template, by measuring it.

The hero frame was originally recorded per template as a hand-read object id.
That does not scale to 45 designs, it goes stale the moment Carmen re-exports a
template, and it was already wrong: `Just Listed — Plus Open House — Offered At`
carried `p1_i10`, a band *inside* the photo area, when the photo actually
occupies `p1_i11`. Confirmed by rendering the template and looking at it on
2026-08-11.

So the frame is measured from the presentation instead.

**What the templates actually look like**, established by reading all 45 through
the Slides API rather than assuming:

* **44 of the 45 contain no `image` elements at all.** These are PPTX imports
  and the photo area arrives as a *shape*. Anything hunting for an image finds
  nothing, which is why hero placement had a measured layer for only three
  designs.
* The photo is a **full-bleed band across the top**: it spans the entire slide
  width and is anchored at or near the top edge, with the copy below it.
* Some shapes carry absurd dimensions — one measured 243 x 145 inches on an
  11 x 14 inch slide — so a plain "largest element" rule picks scaling
  artifacts.

Hence the rule below: the largest sane, textless, full-width shape anchored in
the upper half. It reproduces both hand-measurements that were right and
corrects the one that was wrong.

Does not handle: designs whose photo is not a top band, and designs with no
photo at all. Both return None, which the caller must treat as "ask" rather
than "guess" — a wrong frame puts the house behind the text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

#: How much of the slide width a photo well spans. Measured across all 45
#: designs rather than assumed: this began at 0.60 on the belief that the hero
#: is always a full-bleed top band, and that refused 12 designs whose photo is a
#: partial-width block instead — 45%, 51%, 54% and 57% wide, all anchored at the
#: top. Those are photos. The frames that sit below this are headshots, which
#: measure 21% to 34% and sit two thirds of the way down, so the gap between the
#: two groups is wide and this sits inside it.
_MIN_WIDTH_FRACTION: Final[float] = 0.40

#: The photo is a top band, so its upper edge sits in the top half. This is what
#: separates the photo from the large grey copy panel underneath it.
_MAX_TOP_FRACTION: Final[float] = 0.50

#: A shape reporting more than this multiple of the slide is a scaling artifact
#: of the PPTX import, not a real frame. One measured 21x the slide.
_MAX_SANE_MULTIPLE: Final[float] = 3.0

#: Below this fraction of the slide area a top band is a rule or a header strip.
_MIN_AREA_FRACTION: Final[float] = 0.12


@dataclass(frozen=True, slots=True)
class HeroFrame:
    """Where the hero photo belongs, in EMU on the slide."""

    object_id: str
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        """Frame area in square EMU."""
        return self.width * self.height


#: An affine transform as Slides reports it: (scaleX, shearX, translateX,
#: shearY, scaleY, translateY).
_Affine = tuple[float, float, float, float, float, float]

_IDENTITY: Final[_Affine] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _affine_of(element: dict[str, Any]) -> _Affine:
    """Read one element's own transform."""
    t = element.get("transform", {})
    return (
        t.get("scaleX", 1.0),
        t.get("shearX", 0.0),
        t.get("translateX", 0.0),
        t.get("shearY", 0.0),
        t.get("scaleY", 1.0),
        t.get("translateY", 0.0),
    )


def _compose(parent: _Affine, child: _Affine) -> _Affine:
    """Apply a child transform inside its parent's frame.

    Args:
        parent: The group's transform.
        child: The child's transform, expressed relative to the group.

    Returns:
        The child's absolute transform on the slide.

    Raises:
        Nothing.

    Note:
        Standard 2x3 affine composition. A child inside an `elementGroup` reports
        its transform relative to the group, so its raw translate is meaningless
        on its own — which is why anything reading `pageElements` alone cannot
        see where grouped artwork actually sits.
    """
    pa, pb, pc, pd, pe, pf = parent
    ca, cb, cc, cd, ce, cf = child
    return (
        pa * ca + pb * cd,
        pa * cb + pb * ce,
        pa * cc + pb * cf + pc,
        pd * ca + pe * cd,
        pd * cb + pe * ce,
        pd * cc + pe * cf + pf,
    )


def absolute_boxes(
    elements: list[dict[str, Any]], parent: _Affine = _IDENTITY
) -> list[tuple[dict[str, Any], float, float, float, float]]:
    """Every element with its absolute slide position, groups included.

    Args:
        elements: A `pageElements` list, or a group's children.
        parent: The enclosing group's transform.

    Returns:
        `(element, x, y, width, height)` for every leaf element, with grouped
        children resolved to absolute coordinates.

    Raises:
        Nothing.

    Note:
        Groups themselves are descended into rather than returned: the API
        reports a group's own size as zero, so a group is invisible to any check
        that measures bounds. That blind spot let a face be pasted over the
        decorative artwork it was supposed to sit beside.
    """
    out: list[tuple[dict[str, Any], float, float, float, float]] = []
    for element in elements:
        here = _compose(parent, _affine_of(element))
        group = element.get("elementGroup")
        if group:
            out.extend(absolute_boxes(group.get("children", []), here))
            continue
        size = element.get("size", {})
        width = size.get("width", {}).get("magnitude", 0.0) * here[0]
        height = size.get("height", {}).get("magnitude", 0.0) * here[4]
        out.append((element, here[2], here[5], width, height))
    return out


def _element_bounds(element: dict[str, Any]) -> tuple[float, float, float, float]:
    """Absolute position and rendered size of one page element.

    Args:
        element: A `pageElements` entry.

    Returns:
        `(x, y, width, height)` in EMU. Size is multiplied by the transform's
        scale, because Slides reports an element's intrinsic size separately
        from the scale applied to it, and the intrinsic figure alone is
        meaningless — the hero on one template is stored as 20320000 EMU square
        and scaled to about half that.

    Raises:
        Nothing.
    """
    size = element.get("size", {})
    transform = element.get("transform", {})
    width = size.get("width", {}).get("magnitude", 0.0) * transform.get("scaleX", 1.0)
    height = size.get("height", {}).get("magnitude", 0.0) * transform.get("scaleY", 1.0)
    return (
        transform.get("translateX", 0.0),
        transform.get("translateY", 0.0),
        width,
        height,
    )


def _carries_text(element: dict[str, Any]) -> bool:
    """Whether a shape has any non-whitespace text in it.

    Args:
        element: A `pageElements` entry.

    Returns:
        True if the shape renders words. A frame that holds copy is never the
        photo, whatever its size.

    Raises:
        Nothing.
    """
    runs = element.get("shape", {}).get("text", {}).get("textElements", [])
    written = "".join(run.get("textRun", {}).get("content", "") for run in runs if "textRun" in run)
    return bool(written.strip())


def _is_filled(element: dict[str, Any]) -> bool:
    """Whether a shape is painted with its own colour or gradient.

    Args:
        element: A `pageElements` entry.

    Returns:
        True when the shape carries a real background fill. The photo well on
        these PPTX imports has an empty `shapeBackgroundFill`; a contact card or
        a tint band has a `solidFill`. Measured on the live templates
        2026-08-11.

    Raises:
        Nothing.
    """
    fill = element.get("shape", {}).get("shapeProperties", {}).get("shapeBackgroundFill", {})
    if not fill:
        return False
    # An explicitly NOT_RENDERED fill is not painted, whatever else it carries.
    if fill.get("propertyState") == "NOT_RENDERED":
        return False
    return any(key in fill for key in ("solidFill", "gradientFill", "stretchedPictureFill"))


def find_hero_frame(
    page: dict[str, Any], slide_width: float, slide_height: float
) -> HeroFrame | None:
    """Measure where the hero photo goes on one slide.

    Args:
        page: A `slides[n]` entry from a presentations.get response.
        slide_width: Slide width in EMU.
        slide_height: Slide height in EMU.

    Returns:
        The frame, or None when no candidate is convincing. None means ask,
        never guess: putting the photo in the wrong frame hides the design
        behind it or buries the house under the copy panel.

    Raises:
        Nothing.
    """
    if slide_width <= 0 or slide_height <= 0:
        return None

    slide_area = slide_width * slide_height
    best: HeroFrame | None = None

    for element in page.get("pageElements", []):
        # A group is not a candidate: deleting one removes everything inside it,
        # which is how an entire design vanished once.
        if "shape" not in element or "elementGroup" in element:
            continue
        if element.get("shape", {}).get("shapeType") == "TEXT_BOX":
            continue
        if _carries_text(element):
            continue

        # A shape painted with a colour is a card or a panel, not a photo well.
        # The photo placeholder arrives from the PPTX import with an *empty*
        # shapeBackgroundFill. This is what separates the hero from the large
        # white contact card sitting directly beneath it, which passes every
        # geometric test and is sometimes the bigger of the two.
        if _is_filled(element):
            continue

        x, y, width, height = _element_bounds(element)
        if width <= 0 or height <= 0:
            continue
        if width > slide_width * _MAX_SANE_MULTIPLE or height > slide_height * _MAX_SANE_MULTIPLE:
            continue
        if width < slide_width * _MIN_WIDTH_FRACTION:
            continue
        if y > slide_height * _MAX_TOP_FRACTION:
            continue
        if (width * height) < slide_area * _MIN_AREA_FRACTION:
            continue

        candidate = HeroFrame(element["objectId"], x, y, width, height)
        # Prefer the largest: where two full-width bands nest, the outer one is
        # the photo and the inner is a crop guide or a tint band over it. That
        # is the case the hand-measurement got backwards.
        if best is None or candidate.area > best.area:
            best = candidate

    return best


#: A headshot is roughly square. Wider than this and it is a banner; taller and
#: it is a side panel.
_HEADSHOT_ASPECT: Final[tuple[float, float]] = (0.60, 1.70)

#: It is a portrait, not a hero: big enough to be a face, not the whole design.
_HEADSHOT_MIN_WIDTH_FRACTION: Final[float] = 0.10
_HEADSHOT_MAX_WIDTH_FRACTION: Final[float] = 0.60


def find_headshot_frame(
    page: dict[str, Any],
    slide_width: float,
    slide_height: float,
    exclude_object_id: str = "",
) -> HeroFrame | None:
    """Measure where the agent's headshot goes on one slide.

    The sample face is the most visible thing Gable gets wrong: a flyer went out
    carrying one agent's name beside a different agent's photograph. The roster
    already stores a headshot URL per agent and nothing ever used it, because
    replacing it is an image operation and every fill was text.

    Args:
        page: A `slides[n]` entry from a presentations.get response.
        slide_width: Slide width in EMU.
        slide_height: Slide height in EMU.
        exclude_object_id: The hero frame, so the photo well is not mistaken for
            a face on designs where the hero is itself square.

    Returns:
        The frame, or None when no candidate is convincing. None means leave the
        design alone rather than paste a face over something else.

    Raises:
        Nothing.
    """
    if slide_width <= 0 or slide_height <= 0:
        return None

    best: HeroFrame | None = None
    for element in page.get("pageElements", []):
        if "shape" not in element or "elementGroup" in element:
            continue
        if element.get("objectId") == exclude_object_id:
            continue
        if element.get("shape", {}).get("shapeType") == "TEXT_BOX":
            continue
        if _carries_text(element) or _is_filled(element):
            continue

        x, y, width, height = _element_bounds(element)
        if width <= 0 or height <= 0:
            continue
        if not (
            slide_width * _HEADSHOT_MIN_WIDTH_FRACTION
            <= width
            <= slide_width * _HEADSHOT_MAX_WIDTH_FRACTION
        ):
            continue
        low, high = _HEADSHOT_ASPECT
        if not (low <= width / height <= high):
            continue

        candidate = HeroFrame(element["objectId"], x, y, width, height)
        # A frame with artwork sitting on top of it is not the headshot well.
        # Replacing one covered the decorative speech-tail on the Just Sold
        # design, because the face was drawn over the thing that was drawn over
        # the frame. Verified on a rendered flyer 2026-08-11.
        if _is_overlaid(page, candidate):
            continue
        if best is None or candidate.area > best.area:
            best = candidate
    return best


#: How much of a frame another element may cover before the frame is treated as
#: sitting underneath artwork rather than being a free photo well.
_MAX_OVERLAP_FRACTION: Final[float] = 0.25


def _is_overlaid(page: dict[str, Any], frame: HeroFrame) -> bool:
    """Whether another element covers a meaningful part of this frame.

    Args:
        page: The slide the frame lives on.
        frame: The candidate frame.

    Returns:
        True when some other element overlaps more than a quarter of it. Such a
        frame is behind the design rather than a slot in it, and pasting a face
        into it hides whatever was on top.

    Raises:
        Nothing.
    """
    if frame.area <= 0:
        return False

    # Only what is drawn ON TOP of the frame can be covered by a photo placed
    # into it. `pageElements` is in z-order, so everything before the frame sits
    # behind it and is irrelevant. Checking every element regardless rejected 29
    # of the 45 designs — measured, not guessed — because a headshot well
    # naturally sits over a background panel that it is in no danger of hiding.
    elements = page.get("pageElements", [])
    order = [e.get("objectId") for e in elements]
    try:
        frame_index = order.index(frame.object_id)
    except ValueError:
        frame_index = 0
    above = elements[frame_index + 1 :]

    for element, x, y, width, height in absolute_boxes(above):
        if element.get("objectId") == frame.object_id:
            continue
        if width <= 0 or height <= 0:
            continue
        overlap_w = min(frame.x + frame.width, x + width) - max(frame.x, x)
        overlap_h = min(frame.y + frame.height, y + height) - max(frame.y, y)
        if overlap_w <= 0 or overlap_h <= 0:
            continue
        # Measured against whichever element is smaller. A first attempt used
        # the frame's own area and missed the case that motivated this: a small
        # decorative speech-tail sitting on a large headshot well never covers a
        # quarter of the well, but the well covers nearly all of it — and the
        # face still lands on top of the tail.
        smaller = min(frame.area, width * height)
        if smaller > 0 and (overlap_w * overlap_h) / smaller > _MAX_OVERLAP_FRACTION:
            return True
    return False
