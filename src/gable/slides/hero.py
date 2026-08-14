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

Does not handle: designs whose hero photo is not a top band, and designs with
no hero photo at all. Both return None, which the caller must treat as "ask"
rather than "guess" — a wrong frame puts the house behind the text. Agent
portrait slots are separate: an imported empty shape or an existing Slides
image can be replaced only when its measured geometry is unambiguous.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from gable.slides import fields
from gable.slides.elements import text_content

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

# A smaller empty guide fully inside a hero well occurs in imported templates.
# It is safe to keep the clearly larger outer well, but two similarly sized or
# separate candidates are an ambiguity, not permission to pick the largest.
_MAX_NESTED_GUIDE_AREA_FRACTION: Final[float] = 0.60
_MIN_NESTED_GUIDE_CONTAINMENT: Final[float] = 0.95


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


def _axis_aligned_positive(element: dict[str, Any]) -> bool:
    """Return whether an element can be replaced by an axis-aligned image."""
    transform = element.get("transform", {})
    try:
        return (
            float(transform.get("scaleX", 1.0)) > 0
            and float(transform.get("scaleY", 1.0)) > 0
            and abs(float(transform.get("shearX", 0.0))) < 1e-9
            and abs(float(transform.get("shearY", 0.0))) < 1e-9
        )
    except (TypeError, ValueError):
        return False


def _overlap_area(left: HeroFrame, right: HeroFrame) -> float:
    """Return rectangular overlap between two axis-aligned frame candidates."""
    width = min(left.x + left.width, right.x + right.width) - max(left.x, right.x)
    height = min(left.y + left.height, right.y + right.height) - max(left.y, right.y)
    return max(0.0, width) * max(0.0, height)


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


#: The photo well for each Carmen-maintained design, where the geometric search
#: cannot choose. Every PPTX import carries a second unfilled, untexted shape
#: overlapping the photo band, so `find_hero_frame` sees two candidates and
#: correctly refuses. The second shape is *not* disposable: deleting Sold's
#: removed the white panel behind the Corner House logo and left it washed out
#: over the brickwork.
#:
#: An earlier generation of hand-read ids was abandoned because one of three was
#: wrong. These are different in the ways that caused that: there are six
#: designs rather than forty-five, and each id was measured against that
#: template's own render rather than read by eye — the rejected candidate in
#: four cases contains 9-20% near-white pixels because it also covers the logo
#: strip, while the chosen one is pure photograph.
#:
#: Under Contract was the exception, settled instead by following lawn colour to
#: 66% of the slide, and that method picked the wrong shape. Corrected
#: 2026-08-14 from `p1_i88` to `p1_i85` after a live run clipped the word
#: "Realty" out of the logo. The test below pins the property that separates
#: them, and it is geometric rather than pictorial: the hero sits IN FRONT of
#: the logo in z-order, `p1_i88` starts 95,212 EMU above the logo's bottom edge,
#: so filling it necessarily paints over the logo's last line. `p1_i85` starts
#: 285,795 EMU below the logo and covers nothing behind it — which is exactly
#: what the other five recorded wells do.
#:
#: This is a hint, never an authority. `find_hero_frame` re-measures the named
#: shape and falls back to the geometric search when it is absent or implausible,
#: so a redesigned template degrades to "ask" rather than to a wrong frame.
HERO_OBJECT_IDS: Final[dict[str, str]] = {
    "sold": "p1_i87",
    "under contract": "p1_i85",
    "open house": "p1_i104",
    "new listing": "p1_i92",
    "new listing with open house": "p1_i92",
    "client review post": "p1_i90",
}


def _named_hero_frame(
    page: dict[str, Any], slide_width: float, slide_height: float, object_id: str
) -> HeroFrame | None:
    """Measure one named shape, or return None when it is unusable.

    Args:
        page: A `slides[n]` entry from a presentations.get response.
        slide_width: Slide width in EMU.
        slide_height: Slide height in EMU.
        object_id: The recorded photo-well id for this design.

    Returns:
        The measured frame, or None when the shape is missing, is a group, is
        not axis-aligned, or reports implausible bounds. Every None sends the
        caller back to the geometric search.

    Raises:
        Nothing.
    """
    for element in page.get("pageElements", []):
        if element.get("objectId") != object_id:
            continue
        if "shape" not in element or "elementGroup" in element:
            return None
        if not _axis_aligned_positive(element):
            return None
        x, y, width, height = _element_bounds(element)
        if width <= 0 or height <= 0:
            return None
        if width > slide_width * _MAX_SANE_MULTIPLE or height > slide_height * _MAX_SANE_MULTIPLE:
            return None
        return HeroFrame(object_id, x, y, width, height)
    return None


def find_hero_frame(
    page: dict[str, Any],
    slide_width: float,
    slide_height: float,
    template_label: str = "",
) -> HeroFrame | None:
    """Measure where the hero photo goes on one slide.

    Args:
        page: A `slides[n]` entry from a presentations.get response.
        slide_width: Slide width in EMU.
        slide_height: Slide height in EMU.
        template_label: The design's name. When it matches `HERO_OBJECT_IDS`
            the recorded shape is re-measured and used; anything unrecorded,
            missing, or implausible falls through to the geometric search.

    Returns:
        The frame, or None when no candidate is convincing. None means ask,
        never guess: putting the photo in the wrong frame hides the design
        behind it or buries the house under the copy panel.

    Raises:
        Nothing.
    """
    if slide_width <= 0 or slide_height <= 0:
        return None

    recorded = HERO_OBJECT_IDS.get(" ".join(template_label.split()).casefold(), "")
    if recorded:
        named = _named_hero_frame(page, slide_width, slide_height, recorded)
        if named is not None:
            return named

    slide_area = slide_width * slide_height
    candidates: list[HeroFrame] = []

    for element in page.get("pageElements", []):
        # A group is not a candidate: deleting one removes everything inside it,
        # which is how an entire design vanished once.
        if "shape" not in element or "elementGroup" in element:
            continue
        if element.get("shape", {}).get("shapeType") == "TEXT_BOX":
            continue
        if _carries_text(element):
            continue
        if not _axis_aligned_positive(element):
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
        candidates.append(candidate)

    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate.area, reverse=True)
    best = candidates[0]
    for other in candidates[1:]:
        # ASSUMPTION: an imported inner crop guide is materially smaller and at
        # least 95% contained by the actual well. The final render remains the
        # backstop; a separate or similarly sized candidate is refused here.
        if (
            other.area > best.area * _MAX_NESTED_GUIDE_AREA_FRACTION
            or _overlap_area(best, other) < other.area * _MIN_NESTED_GUIDE_CONTAINMENT
        ):
            return None
    return best


#: A headshot is roughly square. Wider than this and it is a banner; taller and
#: it is a side panel.
_HEADSHOT_ASPECT: Final[tuple[float, float]] = (0.60, 1.70)

#: It is a portrait, not a hero: big enough to be a face, not the whole design.
_HEADSHOT_MIN_WIDTH_FRACTION: Final[float] = 0.10
_HEADSHOT_MAX_WIDTH_FRACTION: Final[float] = 0.60

# An existing Slides image is not self-describing. Logos, QR codes and
# secondary property photos can all be square and portrait-sized, so geometry
# alone cannot authorize deleting one. A sample portrait must sit in the same
# measured contact-card region as a recognised agent name and at least one
# phone, email or title field.
_AGENT_CARD_FIELDS: Final[frozenset[str]] = frozenset(
    {"agent_name", "agent_phone", "agent_email", "agent_title"}
)
_AGENT_CARD_SECONDARY_FIELDS: Final[frozenset[str]] = _AGENT_CARD_FIELDS - {"agent_name"}
_AGENT_CARD_MAX_HORIZONTAL_GAP: Final[float] = 0.15
_AGENT_CARD_MAX_VERTICAL_GAP: Final[float] = 0.18
_AGENT_CARD_MAX_WIDTH: Final[float] = 0.65
_AGENT_CARD_MAX_HEIGHT: Final[float] = 0.45


def _rectangle_gap(left: HeroFrame, right: HeroFrame) -> tuple[float, float]:
    """Return the horizontal and vertical edge gaps between two boxes."""
    horizontal = max(right.x - (left.x + left.width), left.x - (right.x + right.width), 0.0)
    vertical = max(right.y - (left.y + left.height), left.y - (right.y + right.height), 0.0)
    return horizontal, vertical


def _same_agent_card(
    page: dict[str, Any],
    candidate: HeroFrame,
    slide_width: float,
    slide_height: float,
    agent_values: Mapping[str, str] | None,
) -> bool:
    """Require deterministic agent-field context before replacing an image."""
    boxes = absolute_boxes(page.get("pageElements", []))
    written = [text_content(element) for element, *_ in boxes if text_content(element)]
    resolution = fields.resolve(written)
    literal_fields: dict[str, str] = {}
    for field_name in _AGENT_CARD_FIELDS:
        primary = resolution.fields.get(field_name)
        if primary:
            literal_fields[primary] = field_name
        for literal in resolution.also.get(field_name, ()):
            literal_fields[literal] = field_name
        expected = (agent_values or {}).get(field_name, "").strip()
        if expected:
            normal_expected = " ".join(expected.split())
            for literal in written:
                if " ".join(literal.split()) == normal_expected:
                    literal_fields[literal] = field_name

    nearby: list[tuple[str, HeroFrame]] = []
    for element, x, y, width, height in boxes:
        matched_field = literal_fields.get(text_content(element))
        if not matched_field or width <= 0 or height <= 0:
            continue
        field_box = HeroFrame(str(element.get("objectId") or ""), x, y, width, height)
        horizontal, vertical = _rectangle_gap(candidate, field_box)
        if (
            horizontal <= slide_width * _AGENT_CARD_MAX_HORIZONTAL_GAP
            and vertical <= slide_height * _AGENT_CARD_MAX_VERTICAL_GAP
        ):
            nearby.append((matched_field, field_box))

    roles = {field_name for field_name, _box in nearby}
    if "agent_name" not in roles or not roles.intersection(_AGENT_CARD_SECONDARY_FIELDS):
        return False

    relevant = [
        box
        for field_name, box in nearby
        if field_name == "agent_name" or field_name in _AGENT_CARD_SECONDARY_FIELDS
    ]
    left = min([candidate.x, *(box.x for box in relevant)])
    top = min([candidate.y, *(box.y for box in relevant)])
    right = max([candidate.x + candidate.width, *(box.x + box.width for box in relevant)])
    bottom = max([candidate.y + candidate.height, *(box.y + box.height for box in relevant)])
    return (
        right - left <= slide_width * _AGENT_CARD_MAX_WIDTH
        and bottom - top <= slide_height * _AGENT_CARD_MAX_HEIGHT
    )


def headshot_frames(
    page: dict[str, Any],
    slide_width: float,
    slide_height: float,
    exclude_object_id: str = "",
    agent_values: Mapping[str, str] | None = None,
) -> tuple[HeroFrame, ...]:
    """Return every safe, plausible agent-headshot well on one slide.

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
        agent_values: Exact filled contact values when reading a copied flyer.
            Source placeholders are resolved directly; filled real names are
            accepted only when they equal the current run's known values.

    Returns:
        All plausible frames, largest first. The caller may act only when there
        is exactly one; two candidates can represent two agents and choosing
        either would attach the wrong face.

    Raises:
        Nothing.
    """
    if slide_width <= 0 or slide_height <= 0:
        return ()

    candidates: list[HeroFrame] = []
    for element in page.get("pageElements", []):
        # Imported sources represent an agent slot in either of two ways: an
        # empty shape or the sample portrait itself as a Slides image. A group
        # remains unsafe because a newly created image cannot be put back at a
        # child's exact group-local z-order boundary.
        is_image = "image" in element
        is_shape = "shape" in element
        if (not is_shape and not is_image) or "elementGroup" in element:
            continue
        if element.get("objectId") == exclude_object_id:
            continue
        if is_shape:
            if element.get("shape", {}).get("shapeType") == "TEXT_BOX":
                continue
            if _carries_text(element) or _is_filled(element):
                continue
        if not _axis_aligned_positive(element):
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
        if is_image and not _same_agent_card(
            page,
            candidate,
            slide_width,
            slide_height,
            agent_values,
        ):
            continue
        # A frame with artwork sitting on top of it is not the headshot well.
        # Replacing one covered the decorative speech-tail on the Just Sold
        # design, because the face was drawn over the thing that was drawn over
        # the frame. Verified on a rendered flyer 2026-08-11.
        if _is_overlaid(page, candidate, _MAX_HEADSHOT_OVERLAP_FRACTION):
            continue
        candidates.append(candidate)
    return tuple(sorted(candidates, key=lambda candidate: candidate.area, reverse=True))


def find_headshot_frame(
    page: dict[str, Any],
    slide_width: float,
    slide_height: float,
    exclude_object_id: str = "",
    agent_values: Mapping[str, str] | None = None,
) -> HeroFrame | None:
    """Return the one unambiguous headshot well, or None."""
    candidates = headshot_frames(
        page,
        slide_width,
        slide_height,
        exclude_object_id,
        agent_values,
    )
    return candidates[0] if len(candidates) == 1 else None


#: How much of a frame another element may cover before the frame is treated as
#: sitting underneath artwork rather than being a free photo well.
_MAX_OVERLAP_FRACTION: Final[float] = 0.25

#: How much of an overlapping element must lie inside a frame before it counts
#: as sitting *on* that frame rather than clipping its edge. A cut-out portrait
#: has a transparent margin, so neighbouring text and title bands routinely
#: touch its bounding box without ever covering the person.
_MOSTLY_INSIDE_FRACTION: Final[float] = 0.50


#: A replacement image is appended to the page, which puts it at the top of the
#: z-order — above artwork that was originally drawn over its frame. So a face
#: covering meaningful artwork is wrong, and the headshot tolerance remains much
#: tighter than the hero's. The live Sold source intentionally lets the address
#: panel cross 3.16% of its portrait placeholder at the upper-right corner; a 2%
#: ceiling discarded that real slot and left the sample agent's face in place.
#: Four percent admits that measured edge overlap while still rejecting a small
#: decorative tail that is substantially covered by the portrait.
_MAX_HEADSHOT_OVERLAP_FRACTION: Final[float] = 0.04


def _is_overlaid(
    page: dict[str, Any], frame: HeroFrame, tolerance: float = _MAX_OVERLAP_FRACTION
) -> bool:
    """Whether another element covers a meaningful part of this frame.

    Args:
        page: The slide the frame lives on.
        frame: The candidate frame.
        tolerance: How much of the frame another element may cover before it
            counts as overlaid.

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
        other_area = width * height
        if other_area <= 0:
            continue
        overlap = overlap_w * overlap_h
        # Two different questions, and using one answer for both was wrong.
        #
        # "Is something sitting ON this well?" is answered by how much of the
        # *other* element lies inside the frame. A small decorative speech-tail
        # sits entirely on a large well, never covers a quarter of it, and the
        # face still lands on top of it — that is what the tight tolerance is
        # for, and it only makes sense for an element that is mostly inside.
        #
        # "Is this well buried under artwork?" is answered by how much of the
        # *frame* is covered, and it is the only sensible question for an
        # element that merely clips the edge. Applying the tight tolerance to
        # those rejected the headshot on New Listing and Under Contract: the
        # "Under Contract" title band and the "4 Bedrooms" line each graze the
        # cut-out's transparent margin by under 8% of the frame while lying
        # almost entirely outside it. A neighbour is not an occlusion.
        if overlap / other_area > _MOSTLY_INSIDE_FRACTION:
            if overlap / other_area > tolerance:
                return True
        elif overlap / frame.area > _MAX_OVERLAP_FRACTION:
            return True
    return False
