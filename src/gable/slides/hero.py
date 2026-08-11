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

#: A hero band spans essentially the whole slide. Below this the shape is a
#: panel or a card, not the photo.
_MIN_WIDTH_FRACTION: Final[float] = 0.60

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
        if best is None or candidate.area > best.area:
            best = candidate
    return best
