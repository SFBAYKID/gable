"""Measuring a template exactly once, so nothing has to be guessed again.

Every fragile thing Gable does today is a fresh guess on every run: which shape
is the photo well, which literal receives the price, how wide a box really is.
The guesses are re-derived per listing, so a wrong one is wrong on every flyer
and a right one is never written down.

This module turns a template into a **measurement** — one complete, ordered
description of the design — and two fingerprints over it. The measurement is
stored once and reused; the fingerprints say whether the stored one is still
true. A template whose fingerprint has not moved does not need looking at
again, which is what makes "measure once" safe rather than merely cheaper.

Two fingerprints, because they answer different questions:

* ``structural`` covers everything that defines the certified design — position,
  size, type, grouping, z-order, fonts, colours, alignment, autofit and the
  literal text. Any change to it means the stored measurement describes a design
  that no longer exists, so the version must be re-confirmed.
* ``geometry`` covers coordinates and sizes alone. It is not a gate; it exists so
  a change report can say *the price band moved 9pt down* instead of *something
  changed*.

Numbers are rounded before hashing — EMU to whole units, points to one decimal.
Slides returns floats that differ in the last bits between reads of an unchanged
file, and an unrounded hash would invent a new version every poll.

Does not handle: deciding whether a design is any *good*. Whether real values fit
their boxes is `feasibility`, and whether Carmen approves is confirmation. This
module only records what is there, exactly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from gable.slides.hero import absolute_boxes, find_headshot_frame, find_hero_frame

#: Slides reports 914400 EMU per inch. Kept here so callers reading a
#: measurement never need to import a second module to interpret it.
EMU_PER_INCH: Final[int] = 914400

#: Points are rounded to this many decimals before hashing. One decimal is finer
#: than any font size Slides actually stores and coarse enough to absorb the
#: float noise that differs between two reads of the same unchanged file.
_POINT_DECIMALS: Final[int] = 1


@dataclass(frozen=True, slots=True)
class TextRun:
    """One styled run of text inside a shape."""

    text: str
    font_family: str
    font_size_pt: float
    bold: bool
    italic: bool
    weight: int
    colour: str


@dataclass(frozen=True, slots=True)
class ElementSpec:
    """One page element, measured absolutely.

    Position and size are absolute on the slide with any enclosing group's
    transform already composed in, because a grouped child's raw transform is
    relative to its group and meaningless on its own.
    """

    object_id: str
    kind: str
    z_index: int
    x_emu: int
    y_emu: int
    width_emu: int
    height_emu: int
    rotation: float
    group_path: tuple[str, ...]
    text: str
    runs: tuple[TextRun, ...]
    paragraph_alignment: str
    content_alignment: str
    autofit_type: str
    font_scale: float
    fill: str
    outline: str

    @property
    def right_emu(self) -> int:
        """The element's right edge, which is what a neighbour collides with."""
        return self.x_emu + self.width_emu

    @property
    def centre_x_emu(self) -> float:
        """Horizontal centre, for centring checks."""
        return self.x_emu + self.width_emu / 2

    @property
    def centre_y_emu(self) -> float:
        """Vertical centre.

        The master design centres contact text on its icon's midline, so
        this is the value comparison uses.
        """
        return self.y_emu + self.height_emu / 2


@dataclass(frozen=True, slots=True)
class FrameSpec:
    """Where an image belongs. Immutable for the life of a template version."""

    object_id: str
    x_emu: int
    y_emu: int
    width_emu: int
    height_emu: int

    @property
    def aspect(self) -> float:
        """Width over height.

        Measured across the deck this ranges 0.55 to 2.17, which is why no
        single hero size can be correct for every design.
        """
        return self.width_emu / self.height_emu if self.height_emu else 0.0


@dataclass(frozen=True, slots=True)
class TemplateMeasurement:
    """Everything known about one revision of one template."""

    slide_width_emu: int
    slide_height_emu: int
    page_ids: tuple[str, ...]
    elements: tuple[ElementSpec, ...]
    hero: FrameSpec | None
    headshot: FrameSpec | None
    structural_fingerprint: str = ""
    geometry_fingerprint: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def by_id(self, object_id: str) -> ElementSpec | None:
        """The element with this id, or None.

        Args:
            object_id: A Slides object id.

        Returns:
            The matching `ElementSpec`, or None when the id is absent.

        Raises:
            Nothing.
        """
        return next((e for e in self.elements if e.object_id == object_id), None)

    def literals(self) -> dict[str, str]:
        """Every element that holds text, keyed by object id.

        Args:
            None.

        Returns:
            Object id to its exact text. These are the literals a fill replaces,
            so they are stored rather than re-read per run.

        Raises:
            Nothing.
        """
        return {e.object_id: e.text for e in self.elements if e.text}


def _round_emu(value: float) -> int:
    """EMU as a whole number. Sub-EMU precision is float noise, not design."""
    return round(value)


def _round_pt(value: float) -> float:
    """A font size at the precision Slides actually means."""
    return round(float(value), _POINT_DECIMALS)


def _colour_of(fill: dict[str, Any]) -> str:
    """A fill expressed as a comparable string.

    Args:
        fill: A `solidFill` mapping, or anything else.

    Returns:
        `#rrggbb` for an explicit rgb colour, `theme:NAME` for a theme
        reference, or an empty string when there is no solid colour. PPTX
        imports use both forms, so both must be captured — a design whose
        accent moves from one theme colour to another has changed even though
        no coordinate did.

    Raises:
        Nothing.
    """
    solid = fill.get("solidFill") if isinstance(fill, dict) else None
    if not isinstance(solid, dict):
        return ""
    colour = solid.get("color", {})
    themed = colour.get("themeColor")
    if themed:
        return f"theme:{themed}"
    rgb = colour.get("rgbColor")
    if not isinstance(rgb, dict):
        return ""
    parts = (rgb.get("red", 0.0), rgb.get("green", 0.0), rgb.get("blue", 0.0))
    return "#" + "".join(f"{round(float(c) * 255):02x}" for c in parts)


def _runs_of(element: dict[str, Any]) -> tuple[TextRun, ...]:
    """Every styled run in a shape, in reading order.

    Args:
        element: A `pageElements` entry.

    Returns:
        A tuple of `TextRun`. Empty when the element holds no text. Runs are
        kept separate rather than merged because a design that bolds one word
        differs from one that does not, and the fingerprint must see that.

    Raises:
        Nothing.
    """
    text_body = element.get("shape", {}).get("text", {})
    runs: list[TextRun] = []
    for item in text_body.get("textElements", []):
        run = item.get("textRun")
        if not run:
            continue
        content = str(run.get("content", ""))
        if not content.strip():
            continue
        style = run.get("style", {}) or {}
        weighted = style.get("weightedFontFamily", {}) or {}
        runs.append(
            TextRun(
                text=content,
                font_family=str(style.get("fontFamily") or weighted.get("fontFamily") or ""),
                font_size_pt=_round_pt(style.get("fontSize", {}).get("magnitude", 0.0) or 0.0),
                bold=bool(style.get("bold", False)),
                italic=bool(style.get("italic", False)),
                weight=int(weighted.get("weight", 400) or 400),
                colour=_colour_of(style.get("foregroundColor", {}) or {}),
            )
        )
    return tuple(runs)


def _alignment_of(element: dict[str, Any]) -> str:
    """The paragraph alignment, read from the first paragraph marker."""
    for item in element.get("shape", {}).get("text", {}).get("textElements", []):
        marker = item.get("paragraphMarker")
        if marker:
            return str((marker.get("style", {}) or {}).get("alignment", "") or "")
    return ""


def _kind_of(element: dict[str, Any]) -> str:
    """What sort of element this is, in one comparable word."""
    if "image" in element:
        return "image"
    if "line" in element:
        # A divider rule is a Line, not a Shape, and needs updateLineProperties
        # rather than updateShapeProperties. Recording the distinction keeps a
        # later edit from sending the wrong request type.
        return "line"
    if "table" in element:
        return "table"
    shape = element.get("shape")
    if isinstance(shape, dict):
        return f"shape:{shape.get('shapeType', 'UNKNOWN')}"
    return "unknown"


def _rotation_of(element: dict[str, Any]) -> float:
    """Rotation in degrees, derived from the transform's shear terms."""
    import math

    transform = element.get("transform", {}) or {}
    shear_y = float(transform.get("shearY", 0.0) or 0.0)
    scale_x = float(transform.get("scaleX", 1.0) or 1.0)
    if scale_x == 0.0:
        return 0.0
    return round(math.degrees(math.atan2(shear_y, scale_x)), 3)


def _group_paths(
    elements: list[dict[str, Any]], prefix: tuple[str, ...] = ()
) -> dict[str, tuple[str, ...]]:
    """Which groups each leaf element sits inside, outermost first.

    Args:
        elements: A `pageElements` list.
        prefix: The enclosing groups' ids.

    Returns:
        Leaf object id to its chain of enclosing group ids. Group membership is
        part of the design: moving a shape out of a group changes how every
        later transform applies to it, even if its absolute position is
        unchanged.

    Raises:
        Nothing.
    """
    paths: dict[str, tuple[str, ...]] = {}
    for element in elements:
        object_id = str(element.get("objectId") or "")
        group = element.get("elementGroup")
        if group:
            paths.update(_group_paths(group.get("children", []), (*prefix, object_id)))
        else:
            paths[object_id] = prefix
    return paths


def measure(presentation: dict[str, Any]) -> TemplateMeasurement:
    """Describe a template completely, from a `presentations.get` payload.

    Args:
        presentation: The payload exactly as the Slides API returns it.

    Returns:
        A `TemplateMeasurement` with both fingerprints filled in. Pure: the same
        payload always produces the same result, which is what lets the
        fingerprint mean "unchanged" rather than "read at a different moment".

    Raises:
        Nothing. A payload missing pages yields a measurement with no elements,
        which the caller should treat as a failed onboarding rather than as a
        design with nothing on it.
    """
    page_size = presentation.get("pageSize", {}) or {}
    slide_w = _round_emu(page_size.get("width", {}).get("magnitude", 0.0) or 0.0)
    slide_h = _round_emu(page_size.get("height", {}).get("magnitude", 0.0) or 0.0)

    pages = presentation.get("slides", []) or []
    specs: list[ElementSpec] = []
    notes: list[str] = []

    for page in pages:
        page_elements = page.get("pageElements", []) or []
        paths = _group_paths(page_elements)
        # z-order is the order Slides lists elements in; only what comes later
        # is drawn on top, and an overlap check that ignores this is wrong.
        for z_index, (element, x, y, width, height) in enumerate(absolute_boxes(page_elements)):
            object_id = str(element.get("objectId") or "")
            shape = element.get("shape", {}) or {}
            properties = shape.get("shapeProperties", {}) or {}
            autofit = properties.get("autofit", {}) or {}
            runs = _runs_of(element)
            specs.append(
                ElementSpec(
                    object_id=object_id,
                    kind=_kind_of(element),
                    z_index=z_index,
                    x_emu=_round_emu(x),
                    y_emu=_round_emu(y),
                    width_emu=_round_emu(width),
                    height_emu=_round_emu(height),
                    rotation=_rotation_of(element),
                    group_path=paths.get(object_id, ()),
                    text="".join(run.text for run in runs).strip(),
                    runs=runs,
                    paragraph_alignment=_alignment_of(element),
                    content_alignment=str(properties.get("contentAlignment", "") or ""),
                    autofit_type=str(autofit.get("autofitType", "") or ""),
                    font_scale=round(float(autofit.get("fontScale", 1.0) or 1.0), 4),
                    fill=_colour_of(properties.get("shapeBackgroundFill", {}) or {}),
                    outline=_colour_of(
                        (properties.get("outline", {}) or {}).get("outlineFill", {}) or {}
                    ),
                )
            )

    hero = headshot = None
    if pages and slide_w > 0 and slide_h > 0:
        found_hero = find_hero_frame(pages[0], slide_w, slide_h)
        if found_hero is None:
            # ASSUMPTION: a design with no detectable photo well needs a human to
            # point at the right shape. Confirmed by measurement rather than
            # assumed: 3 of 12 sampled templates resolve to no frame at all.
            notes.append("no hero frame detected; needs a human to designate one")
        else:
            hero = FrameSpec(
                found_hero.object_id,
                _round_emu(found_hero.x),
                _round_emu(found_hero.y),
                _round_emu(found_hero.width),
                _round_emu(found_hero.height),
            )
            if found_hero.y < 0 or found_hero.x < 0:
                notes.append("hero frame starts off-slide; confirm this is deliberate bleed")

        found_face = find_headshot_frame(
            pages[0], slide_w, slide_h, exclude_object_id=found_hero.object_id if found_hero else ""
        )
        if found_face is not None:
            headshot = FrameSpec(
                found_face.object_id,
                _round_emu(found_face.x),
                _round_emu(found_face.y),
                _round_emu(found_face.width),
                _round_emu(found_face.height),
            )

    measurement = TemplateMeasurement(
        slide_width_emu=slide_w,
        slide_height_emu=slide_h,
        page_ids=tuple(str(p.get("objectId") or "") for p in pages),
        elements=tuple(specs),
        hero=hero,
        headshot=headshot,
        notes=tuple(notes),
    )
    return TemplateMeasurement(
        slide_width_emu=measurement.slide_width_emu,
        slide_height_emu=measurement.slide_height_emu,
        page_ids=measurement.page_ids,
        elements=measurement.elements,
        hero=measurement.hero,
        headshot=measurement.headshot,
        structural_fingerprint=structural_fingerprint(measurement),
        geometry_fingerprint=geometry_fingerprint(measurement),
        notes=measurement.notes,
    )


def _digest(payload: object) -> str:
    """A stable hash of a canonical JSON rendering."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def structural_fingerprint(measurement: TemplateMeasurement) -> str:
    """A hash over everything that defines the certified design.

    Args:
        measurement: A measurement, with or without its fingerprints set.

    Returns:
        A hex sha256. Changes when any element moves, resizes, changes type,
        grouping, z-order, font, colour, alignment, autofit or literal text —
        which is the set of changes that invalidate a confirmed version.

    Raises:
        Nothing.
    """
    body = {
        "slide": [measurement.slide_width_emu, measurement.slide_height_emu],
        "pages": list(measurement.page_ids),
        "elements": [
            {
                k: v
                for k, v in asdict(element).items()
                # z_index is captured via list order below; including it twice
                # would make an unchanged design hash differently after a
                # no-op reorder of equal-index elements.
                if k != "z_index"
            }
            for element in sorted(measurement.elements, key=lambda e: (e.z_index, e.object_id))
        ],
        "hero": asdict(measurement.hero) if measurement.hero else None,
        "headshot": asdict(measurement.headshot) if measurement.headshot else None,
    }
    return _digest(body)


def geometry_fingerprint(measurement: TemplateMeasurement) -> str:
    """A hash over positions and sizes only.

    Args:
        measurement: A measurement.

    Returns:
        A hex sha256 covering coordinates alone. Used to describe *what kind* of
        change happened — a design whose structural hash moved but whose
        geometry hash did not was restyled, not relaid out.

    Raises:
        Nothing.
    """
    body = [
        [e.object_id, e.x_emu, e.y_emu, e.width_emu, e.height_emu]
        for e in sorted(measurement.elements, key=lambda e: e.object_id)
    ]
    return _digest(body)


def differences(before: TemplateMeasurement, after: TemplateMeasurement) -> list[str]:
    """What changed between two measurements, in words a person can act on.

    Args:
        before: The previously confirmed measurement.
        after: The measurement just taken.

    Returns:
        One line per change, worst first: elements added, removed, moved,
        resized or restyled. Empty when the two describe the same design.

    Raises:
        Nothing.
    """
    old = {e.object_id: e for e in before.elements}
    new = {e.object_id: e for e in after.elements}
    lines: list[str] = []

    for object_id in sorted(set(old) - set(new)):
        lines.append(f"removed {old[object_id].kind} {object_id!r} {old[object_id].text[:40]!r}")
    for object_id in sorted(set(new) - set(old)):
        lines.append(f"added {new[object_id].kind} {object_id!r} {new[object_id].text[:40]!r}")

    for object_id in sorted(set(old) & set(new)):
        a, b = old[object_id], new[object_id]
        label = (b.text[:32] or b.kind) if b.text or b.kind else object_id
        if (a.x_emu, a.y_emu) != (b.x_emu, b.y_emu):
            dx = (b.x_emu - a.x_emu) / EMU_PER_INCH
            dy = (b.y_emu - a.y_emu) / EMU_PER_INCH
            lines.append(f"moved {label!r} by {dx:+.2f}in, {dy:+.2f}in")
        if (a.width_emu, a.height_emu) != (b.width_emu, b.height_emu):
            dw = (b.width_emu - a.width_emu) / EMU_PER_INCH
            dh = (b.height_emu - a.height_emu) / EMU_PER_INCH
            lines.append(f"resized {label!r} by {dw:+.2f}in, {dh:+.2f}in")
        sizes_before = [r.font_size_pt for r in a.runs]
        sizes_after = [r.font_size_pt for r in b.runs]
        if sizes_before != sizes_after:
            lines.append(f"font size on {label!r} {sizes_before} -> {sizes_after}")
        if a.text != b.text:
            lines.append(f"text {a.text[:32]!r} -> {b.text[:32]!r}")
        if (a.paragraph_alignment, a.content_alignment) != (
            b.paragraph_alignment,
            b.content_alignment,
        ):
            lines.append(
                f"alignment on {label!r} "
                f"{a.paragraph_alignment or '-'}/{a.content_alignment or '-'} -> "
                f"{b.paragraph_alignment or '-'}/{b.content_alignment or '-'}"
            )
        if a.fill != b.fill:
            lines.append(f"fill on {label!r} {a.fill or '-'} -> {b.fill or '-'}")
    return lines
