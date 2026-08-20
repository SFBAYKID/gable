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
from dataclasses import dataclass, field
from typing import Any, Final

from gable.photos.fit import assess
from gable.slides import fields, fitting
from gable.slides.elements import (
    descendants,
    text_content,
)
from gable.slides.hero import find_hero_frame, headshot_frames
from gable.slides.measure import _axis_aligned_positive, text_boxes

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
#
# `agent_title` is deliberately absent. Every other entry stands in for a value
# nobody knows yet, so an average-width estimate is the best available test. The
# title is the one field whose value IS known — a title too long for its slot is
# cut back to the credential before anything is written — and that exact word is
# measured against the box's real advance widths by `_title_that_fits`. Keeping
# an estimate here as well blocked every Under Contract run the moment Carmen
# edited that design, on a slot that renders "Realtor" perfectly well.
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

#: Fields whose empty state is the design's own words rather than a gap. The
#: note panel on Under Contract ships reading "Ready to Buy? / DM me to find
#: your next home." — a perfectly good call to action — and a submission with
#: nothing to add about the deal should keep it. Treating that as a missing
#: value stopped Donald Clark's rebuild to ask what the note should say, on a
#: listing whose details column said only "Under Contract".
OPTIONAL_FIELDS: Final[frozenset[str]] = frozenset({"listing_note"})

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
    #: Values the measurement itself changed, for the caller to fill with
    #: instead of the ones it supplied. Empty unless something was adjusted.
    adjusted: dict[str, str] = field(default_factory=dict)

    @property
    def blockers(self) -> tuple[Issue, ...]:
        """Problems that cannot be overridden safely."""
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        """Correctable measured tradeoffs to report with the finished build."""
        return tuple(issue for issue in self.issues if not issue.blocking)


def _readable(text: str) -> str:
    """Make a template literal safe to show under Gable's no-brackets rule."""
    return " ".join(_TOKEN_MARKS.sub("", text).split()) or "an unnamed field"


def _field_for_literal(resolution: fields.Resolution, literal: str) -> str:
    """Return the semantic field name carried by one template literal."""
    for name, primary in resolution.fields.items():
        if primary == literal or literal in resolution.also.get(name, ()):
            return name
    return "text"


def _allowed_lines(field_name: str, replacement: str, box_lines: int) -> int:
    r"""How many lines this value may occupy in its box.

    A single-line field must not WRAP — half an email address on a second line
    is broken, and a name that wraps lands on the title beneath it. A line break
    the designer typed is not wrapping. New Listing draws its counts as
    "4\nBedrooms", so forcing that field onto one line measured a two-line
    label as though it had to fit across one, and refused the design for being
    one per cent too narrow.

    Args:
        field_name: The field being filled.
        replacement: The exact text that will be written.
        box_lines: How many lines the box can hold.

    Returns:
        The line budget: the value's own explicit breaks for a single-line
        field, the box's capacity for anything else.

    Raises:
        Nothing.
    """
    if field_name not in SINGLE_LINE_FIELDS:
        return box_lines
    return max(1, replacement.count("\n") + 1)


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
        _allowed_lines(field_name, replacement, box.lines),
        box.weight,
        box.family,
    )

    if not fit.overflows or not fit.too_small_to_read:
        return None

    current_width = max(1.0, fit.box_width_pt)
    needed_width = fitting.estimate_width_pt(replacement, fit.current_pt, fit.weight, box.family)
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


#: The membership credential these designs draw a title slot for. Every source
#: in Generic Templates types either "Realtor" or "REALTOR" there, which is what
#: the slot was measured and drawn to hold.
_CREDENTIAL: Final[str] = "Realtor"

#: A credential inside a longer professional title: "Listing Manager,
#: Transaction Coordinator & Realtor®" or "REALTOR®, The Kulnich Home Team".
_CREDENTIAL_IN_TITLE: Final[re.Pattern[str]] = re.compile(r"\brealtors?\b", re.IGNORECASE)


def _fits_every_box(
    template_label: str,
    boxes: list[fitting.TextBox],
    replacement: str,
) -> bool:
    """Whether one replacement is readable in every box that carries it."""
    return all(
        _replacement_issue(template_label, "agent_title", box, replacement) is None for box in boxes
    )


def _box_left_blank(resolution: fields.Resolution, values: dict[str, str]) -> str:
    """The field whose design box this run would empty rather than fill.

    Open House sets the date and the time in separate boxes. Row 16's form
    carries "7/11/2026" — a date with no time — so the date box filled and the
    time box was blanked, and the flyer showed the design's own two separators
    with a gap between them. The visual gate refused it, which cost a second
    round trip for something knowable before the copy was ever made.

    Blanking is still the right fallback: the alternative is leaving a previous
    listing's real time on somebody else's flyer. But it is a fallback, and a
    value Gable knows it cannot supply belongs in the one batched ask.

    Args:
        resolution: What each of the design's literals means.
        values: The values this run intends to fill.

    Returns:
        The field name whose box would be emptied, or "" when every box a value
        touches will actually carry something. A field with no value at all is
        not this — that is the ordinary missing-value check above.

    Raises:
        Nothing.
    """
    pairs = fields.replacements(resolution, values)
    blanked = {literal for literal, written in pairs.items() if not written.strip()}
    if not blanked:
        return ""
    return next(
        (
            name
            for name, primary in resolution.fields.items()
            if values.get(name, "").strip() and blanked & {primary, *resolution.also.get(name, ())}
        ),
        "",
    )


def _title_that_fits(
    template_label: str,
    resolution: fields.Resolution,
    values: dict[str, str],
    boxes: list[fitting.TextBox],
) -> tuple[dict[str, str], Issue | None]:
    """Shorten a job title to its credential when the full one cannot fit.

    Two people on the roster carry a title no design has room for: Sara Wolz's
    "Listing Manager, Transaction Coordinator & Realtor" needs about 627 percent
    more width than Under Contract's title slot, and Gina Moore's "REALTOR, The
    Kulnich Home Team" about 391 percent. Both were a hard stop, and neither is
    fixable from Slack — the design would have to be redrawn.

    Their own titles already contain the credential the slot was drawn for, so
    the flyer prints that and the closing message says the longer title was
    dropped. This changes what an agent's flyer says about them, so it is never
    silent and never a guess: the shorter form is taken from the agent's own
    proven title, not invented.

    Args:
        template_label: The design's name, for the sentence Carmen reads.
        resolution: What the design's text means.
        values: The values this run intends to fill.
        boxes: Every measured text box on the design.

    Returns:
        The values that changed — empty when nothing did — and the advisory to
        fold into the closing message, or None when there is nothing to say.

    Raises:
        Nothing.
    """
    title = values.get("agent_title", "").strip()
    literal = resolution.fields.get("agent_title", "")
    if not title or not literal:
        return {}, None
    shorter = _CREDENTIAL if _CREDENTIAL_IN_TITLE.search(title) else ""
    if not shorter or shorter.casefold() == title.casefold():
        return {}, None

    matching = [box for box in boxes if box.text.strip() == literal.strip()]
    # An unmeasurable box is reported on its own further down. Do not shorten a
    # title on the strength of a measurement that was never taken.
    if not matching:
        return {}, None

    def written(value: str) -> str:
        """The exact text a fill would write, capitals and all."""
        return fields.replacements(resolution, {"agent_title": value}).get(literal, value)

    full_written, short_written = written(title), written(shorter)
    fits = _fits_every_box(template_label, matching, full_written)
    if fits or not _fits_every_box(template_label, matching, short_written):
        return {}, None

    note = (
        f"This design's title line has room for one word, so it says {short_written} "
        f"rather than the full {title}."
    )
    return {"agent_title": shorter}, Issue("shortened_agent_title", note, advisory=note)


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

    shared = fields.fields_sharing_a_literal(resolution)
    if shared:
        literal, names = next(iter(sorted(shared.items())))
        readable = " and ".join(name.replace("_", " ") for name in sorted(names))
        issues.append(
            Issue(
                "ambiguous_literal",
                f"I checked the {template_label} design before building. Its {readable} "
                f"sections both read {literal!r}, so I cannot tell which is which and "
                "would have to write the same value into both. Make them different, "
                "then tell me to check it again.",
                blocking=True,
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
    # Fields whose problem has already been named in words Carmen can act on.
    # The width complaint beneath would otherwise fire on the same field and
    # ask for a wider box in the same breath as asking which value to use.
    explained: set[str] = set()
    if values:
        missing = next(
            (
                name
                for name in resolution.fields
                if name not in OPTIONAL_FIELDS and not values.get(name, "").strip()
            ),
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

        blanked = _box_left_blank(resolution, values)
        if blanked and not missing:
            issues.append(
                Issue(
                    f"missing_part_{blanked}",
                    f"I checked the {template_label} design before building. It sets the "
                    f"{blanked.replace('_', ' ')} in its own box, and what I have for this "
                    "listing does not include that part. What should it say? Leave it and "
                    "I will build without it.",
                    blocking=True,
                    status="needs_info",
                )
            )

        # More open houses than the design can say. Effie Fafaleos' 2026-08-20
        # request named three across three days; these designs draw one date and
        # one time. Gable answered "Widen that section, then tell me to check
        # the updated template again", which is a remedy that cannot work --
        # no width holds three different hours in one time box, and a wider box
        # would only have shipped the mangled split. Asking which one to print
        # is a question Carmen can actually answer, in the thread, today.
        occasions = (
            fields.open_house_occasions(values.get("open_house", ""))
            if "open_house" in resolution.fields
            else 0
        )
        if occasions > 1:
            explained.add("open_house")
            issues.append(
                Issue(
                    "several_open_houses",
                    f"This request names {occasions} open houses, and the {template_label} "
                    "design has one date and one time. Which one should I put on the flyer? "
                    "Reply with the day and hours and I will build it.",
                    blocking=True,
                    status="needs_info",
                )
            )

    boxes = text_boxes(presentation)
    adjusted, title_note = _title_that_fits(template_label, resolution, values, boxes)
    if adjusted:
        values = {**values, **adjusted}
        if title_note is not None:
            issues.append(title_note)

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
        if field_name in explained:
            continue
        for box in matching:
            issue = _replacement_issue(template_label, field_name, box, replacement)
            if issue is not None and issue.code not in warned:
                issues.append(issue)
                warned.add(issue.code)

    if photo_size is not None:
        photo_width, photo_height = photo_size
        photo = assess(photo_width, photo_height, hero_width_px, hero_height_px)
        # Either contained path keeps the complete photograph over a same-photo
        # backdrop. Its hypothetical cover crop can be large, but no source edge
        # is actually discarded, so reporting crop loss would be a false claim
        # about what Gable did. This read `needs_small_source_fit`, which stopped
        # being the whole of that set the moment a badly-shaped source was
        # contained too: a rebuilt flyer kept the entire photograph and still
        # said it had center-cropped 57 percent of it away.
        if photo.needs_contained_fit:
            note = (
                "The photo is a different shape from the frame, so I kept all of it "
                "and filled the space around it with a blurred copy of the same "
                "photo rather than cropping the property."
            )
            issues.append(Issue("photo_contained_whole", note, advisory=note))
        elif photo.crop_loss > PHOTO_CROP_WARNING:
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

    return Report(tuple(issues), hero_width_px, hero_height_px, adjusted)


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
