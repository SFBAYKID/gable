"""Reject template and content problems before a flyer copy is created.

Google Slides already exposes the exact geometry of every text box and photo
well.  That data is more reliable for measurement than asking a vision model to
guess coordinates from pixels.  This module uses those measurements to answer
three questions before the build starts:

* can Gable identify the fields and the hero-photo frame safely;
* will this listing's actual values fit at the template's designed type size;
* how much of the supplied photo a frame-aware center crop will discard.

Correctable fit findings become outcome notes, never pre-build questions.  The
rendered-flyer vision pass remains the final gate: it catches effects that
cannot be inferred from rectangles, such as a roofline hidden by a decorative
mask, and stops delivery when the automatic crop did not work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from gable.photos.fit import assess
from gable.slides import fields, fitting
from gable.slides.elements import descendants, font_size_pt, font_weight, text_content
from gable.slides.hero import absolute_boxes, find_hero_frame, headshot_frames

PHOTO_CROP_WARNING: Final[float] = 0.30
_TOKEN_MARKS: Final[re.Pattern[str]] = re.compile(r"[\[\]{}<>]")
_FIELDISH_UNKNOWN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:ADDRESS|AGENT|BATHS?|BEDS?|CLIENT|DATE|EMAIL|HANDLE|MLS|NAME|PHONE|"
    r"PRICE|QUOTE|REVIEW|SQ\.?\s*FT|SQFT|TIME|TITLE|WEBSITE)\b",
    re.IGNORECASE,
)

# A new design is checked with realistic upper-bound content before any listing
# depends on it. These are capacities, not values Gable will ever print. They
# deliberately cover the long-but-normal end of the current roster and form.
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
    "agent_title": 24,
    "social_handle": 28,
    "neighborhood": 32,
    "website": 36,
    "open_house": 38,
}

# These values become confusing or visually broken when they wrap. A tall box
# is not permission to put half an email, phone number, price, or person's name
# on a second line. Addresses, review copy, and open-house wording may be
# intentionally multiline.
SINGLE_LINE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "price",
        "beds",
        "baths",
        "square_feet",
        "agent_name",
        "agent_phone",
        "agent_email",
        "client_name",
        "agent_title",
        "social_handle",
        "neighborhood",
        "website",
    }
)


@dataclass(frozen=True, slots=True)
class Issue:
    """One problem Gable can explain before it creates a flyer."""

    code: str
    say: str
    blocking: bool = False
    status: str = "needs_template"
    advisory: str = ""


@dataclass(frozen=True, slots=True)
class Report:
    """The measured result for one template and one listing."""

    issues: tuple[Issue, ...] = ()
    hero_width_px: int = 0
    hero_height_px: int = 0

    @property
    def blockers(self) -> tuple[Issue, ...]:
        """Problems that cannot be overridden safely."""
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        """Correctable measured tradeoffs to report with the finished build."""
        return tuple(issue for issue in self.issues if not issue.blocking)


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
    except (TypeError, ValueError):
        return False


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
        for element, _x, _y, width, height in absolute_boxes(page.get("pageElements", [])):
            text = text_content(element)
            if not text:
                continue
            size = element.get("size", {})
            intrinsic_height = float(size.get("height", {}).get("magnitude", 0))
            # The group scales the type along with the box, so the rendered
            # point size is the declared one times that same factor.
            scale = height / intrinsic_height if intrinsic_height > 0 else 1.0
            declared = font_size_pt(element)
            size_pt = declared * scale if declared else _implied_font_size_pt(height)
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
                )
            )
    return boxes


def _readable(text: str) -> str:
    """Make a template literal safe to show under Gable's no-brackets rule."""
    return " ".join(_TOKEN_MARKS.sub("", text).split()) or "an unnamed field"


def _field_for_literal(resolution: fields.Resolution, literal: str) -> str:
    """Return the semantic field name carried by one template literal."""
    for name, primary in resolution.fields.items():
        if primary == literal or literal in resolution.also.get(name, ()):
            return name
    return "text"


def _replacement_issue(
    template_label: str,
    field_name: str,
    box: fitting.TextBox,
    replacement: str,
) -> Issue | None:
    """Return only a fit problem Gable cannot solve at a readable size."""
    readable = field_name.replace("_", " ")
    prefix = f"I checked the {template_label} design before building."
    # A value can fit its box and still be unreadable when the source was
    # already drawn at tiny type. The old check only inspected a font size that
    # Gable itself had to shrink, so an existing 6-point dynamic field sailed
    # through preflight unchanged and could be delivered.
    if box.font_size_pt <= fitting.MIN_READABLE_PT:
        return Issue(
            code=f"unreadable_{field_name}",
            say=(
                f"{prefix} The {readable} is already {box.font_size_pt:g} points, "
                f"which is at or below the {fitting.MIN_READABLE_PT:g}-point readability "
                "limit. Increase that text size and widen its section if needed, then "
                "tell me to check the updated template again."
            ),
            blocking=True,
        )

    fit = fitting.fit_for(
        box.object_id,
        replacement,
        box.font_size_pt,
        box.width_emu,
        1 if field_name in SINGLE_LINE_FIELDS else box.lines,
        box.weight,
    )

    if not fit.overflows or not fit.too_small_to_read:
        return None

    current_width = max(1.0, fit.box_width_pt)
    needed_width = fitting.estimate_width_pt(replacement, fit.current_pt, fit.weight)
    extra = max(1, round(((needed_width / current_width) - 1) * 100))
    return Issue(
        code=f"unreadable_{field_name}",
        say=(
            f"{prefix} The {readable} would need about {extra} percent more room, "
            f"and shrinking it enough would take it below the "
            f"{fitting.MIN_READABLE_PT:g}-point readability limit. Widen that section, "
            "then tell me to check the updated template again."
        ),
        blocking=True,
    )


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
                            "that section, then tell me to check the updated template again."
                        ),
                        blocking=True,
                    )
                )
                warned.add(field_name)
                break

    return Report(tuple(issues), structural.hero_width_px, structural.hero_height_px)


def analyze(
    presentation: dict[str, Any],
    template_label: str,
    category: str,
    resolution: fields.Resolution,
    values: dict[str, str],
    *,
    slide_px: tuple[int, int] = (1080, 1350),
    photo_size: tuple[int, int] | None = None,
) -> Report:
    """Inspect a template and this listing's values before any copy is made."""
    issues: list[Issue] = []
    pages = presentation.get("slides", [])
    if len(pages) != 1:
        count = len(pages)
        return Report(
            issues=(
                Issue(
                    "slide_count",
                    f"I checked the {template_label} design before building. It has {count} "
                    "slides, but a flyer must contain exactly one. Remove the extra slides, "
                    "then tell me to check it again.",
                    blocking=True,
                ),
            )
        )

    page_size = presentation.get("pageSize", {})
    slide_width = float(page_size.get("width", {}).get("magnitude", 0) or 0)
    slide_height = float(page_size.get("height", {}).get("magnitude", 0) or 0)
    if slide_width <= 0 or slide_height <= 0:
        return Report(
            issues=(
                Issue(
                    "slide_size",
                    f"I could not measure the page size in the {template_label} design, so I "
                    "cannot prove that its fields or photo will fit. Re-save the template as "
                    "a Google Slides file, then tell me to check it again.",
                    blocking=True,
                ),
            )
        )

    if not resolution.is_usable_for(category):
        issues.append(
            Issue(
                "missing_fields",
                f"I checked the {template_label} design before building, but I could not find "
                "a property-address field I can fill safely. Add a clear address placeholder, "
                "then tell me to check it again.",
                blocking=True,
            )
        )

    suspicious = next(
        (
            literal
            for literal in resolution.unrecognised
            if _TOKEN_MARKS.search(literal) or _FIELDISH_UNKNOWN.search(literal)
        ),
        "",
    )
    if suspicious:
        issues.append(
            Issue(
                "unknown_placeholder",
                f"I found a fillable-looking field called {_readable(suspicious)} in the "
                f"{template_label} design, but I do not know what data belongs there. Rename "
                "it to a field Gable knows, then tell me to check it again.",
                blocking=True,
            )
        )

    frame = find_hero_frame(pages[0], slide_width, slide_height, template_label)
    if frame is None:
        issues.append(
            Issue(
                "missing_photo_frame",
                f"I checked the {template_label} design before building, but I could not "
                "identify exactly one safe main-photo frame. Make that frame a separate, "
                "unfilled shape near the top, then tell me to check it again.",
                blocking=True,
            )
        )
        return Report(tuple(issues))

    hero_width_px = max(1, round(frame.width / slide_width * slide_px[0]))
    hero_height_px = max(1, round(frame.height / slide_height * slide_px[1]))

    # A sample face is not decorative filler. If this source has a recognisable
    # headshot well, a run without the named agent's file must pause before a
    # copy exists; the final visual model cannot know that a plausible portrait
    # belongs to somebody else.
    headshots = headshot_frames(pages[0], slide_width, slide_height, frame.object_id)
    if len(headshots) > 1:
        issues.append(
            Issue(
                "ambiguous_headshot_frame",
                f"I checked the {template_label} design before building, but I found more "
                "than one possible agent-photo spot. I cannot prove which face belongs in "
                "which spot. Keep one separate headshot frame, then tell me to check it again.",
                blocking=True,
            )
        )
    elif values and headshots and not values.get("headshot", "").strip():
        agent = values.get("agent_name", "the agent").strip() or "the agent"
        issues.append(
            Issue(
                "missing_headshot",
                f"I checked the {template_label} design before building. It has an agent "
                f"photo spot, but I could not find a headshot for {agent}. Add that image "
                "to Head Shots, then tell me to run again.",
                blocking=True,
                status="needs_info",
            )
        )

    literals_by_field = {
        name: (primary, *resolution.also.get(name, ()))
        for name, primary in resolution.fields.items()
    }
    # A field inside grouped artwork used to be refused outright, because a
    # child's transform is relative to its group and the measurement was
    # therefore unreliable — New Listing with Open House scales its REALTOR box
    # to 0.75 and was rejected every time. `text_boxes` now composes the parent
    # transforms, so the box is measured as it renders and there is nothing left
    # to refuse. Rotation and shear are a different question and still stop
    # below: those change what "fits" means rather than merely scaling it.

    transformed_field = next(
        (
            name
            for page in pages
            for element in page.get("pageElements", [])
            for name, literals in literals_by_field.items()
            if text_content(element).strip() in {literal.strip() for literal in literals}
            and not _axis_aligned_positive(element)
        ),
        "",
    )
    if transformed_field:
        readable = transformed_field.replace("_", " ")
        issues.append(
            Issue(
                f"unsupported_transform_{transformed_field}",
                f"I checked the {template_label} design before building. Its {readable} "
                "box is rotated, skewed, or mirrored, so I cannot measure its text capacity "
                "exactly. Make that box axis-aligned, then tell me to check it again.",
                blocking=True,
            )
        )

    # A recognised field with no truthful value is already a known failure. Do
    # not create a copy and hope the final vision pass notices its placeholder.
    # New-template certification deliberately supplies no listing values, so
    # this check applies only to an actual run.
    # A person may release this exact block by answering that Gable should build
    # anyway; the runner drops `missing_value_*` blockers in that case. The
    # check still runs so the reason is recorded either way.
    if values:
        missing = next(
            (name for name in resolution.fields if not values.get(name, "").strip()),
            "",
        )
        if missing:
            readable = missing.replace("_", " ")
            issues.append(
                Issue(
                    f"missing_value_{missing}",
                    f"I checked the {template_label} design before building. It has a "
                    f"{readable} section, but I do not have a value for it. What should "
                    "it say? You can also remove that section from the template and tell "
                    "me to check it again.",
                    blocking=True,
                    status="needs_info",
                )
            )

    pairs = fields.replacements(resolution, values)
    all_text = [text_content(element) for element in descendants(pages[0].get("pageElements", []))]
    for literal in pairs:
        total = sum(text.count(literal) for text in all_text)
        standalone = sum(text.strip() == literal.strip() for text in all_text)
        if total == 0 or total != standalone:
            issues.append(
                Issue(
                    "unsafe_replacement",
                    f"I checked the {template_label} design before building. Its "
                    f"{_field_for_literal(resolution, literal).replace('_', ' ')} field is "
                    "embedded in other text, so filling it could change the wrong words. Put "
                    "that field in its own text box, then tell me to check it again.",
                    blocking=True,
                )
            )
            break

    boxes = text_boxes(presentation)
    warned: set[str] = set()
    for literal, replacement in pairs.items():
        matching = [box for box in boxes if box.text.strip() == literal.strip()]
        field_name = _field_for_literal(resolution, literal)
        if not matching:
            issues.append(
                Issue(
                    f"unmeasured_{field_name}",
                    f"I found the {field_name.replace('_', ' ')} in the {template_label} "
                    "design, but I could not measure its text box. Put it in a normal Slides "
                    "text box, then tell me to check it again.",
                    blocking=True,
                )
            )
            continue
        for box in matching:
            issue = _replacement_issue(template_label, field_name, box, replacement)
            if issue is not None and issue.code not in warned:
                issues.append(issue)
                warned.add(issue.code)

    if photo_size is not None:
        photo_width, photo_height = photo_size
        photo = assess(photo_width, photo_height, hero_width_px, hero_height_px)
        # The small-source path contains the complete foreground over a
        # same-photo backdrop. Its hypothetical cover crop can be large, but no
        # source edge is actually discarded, so reporting crop loss would be a
        # false claim about what Gable did.
        if not photo.needs_small_source_fit and photo.crop_loss > PHOTO_CROP_WARNING:
            percent = round(photo.crop_loss * 100)
            note = (
                f"I center-cropped and fitted the photo to the current frame; about "
                f"{percent} percent fell outside that frame."
            )
            issues.append(
                Issue(
                    "large_photo_crop",
                    note,
                    advisory=note,
                )
            )

    return Report(tuple(issues), hero_width_px, hero_height_px)


def blocking_after_release(report: Report, allow_blank_fields: bool) -> tuple[Issue, ...]:
    """Blockers that still stand once a person has released the blank ones.

    Chase's rule, 2026-08-13: the sheet is what there is, so a value nobody has
    is Carmen's decision rather than a dead end — she supplies it or says to
    build and fills it in herself. That release covers `missing_value_*` alone.
    An unreadable type size, an unsafe structure, an ambiguous photo well and a
    missing headshot are not waivable and are all still returned here.

    Args:
        report: The measured result for this template and listing.
        allow_blank_fields: Whether a person approved building with unknown
            values left blank.

    Returns:
        The blockers the run must still stop for, in report order.

    Raises:
        Nothing.
    """
    if not allow_blank_fields:
        return report.blockers
    return tuple(issue for issue in report.blockers if not issue.code.startswith("missing_value_"))
