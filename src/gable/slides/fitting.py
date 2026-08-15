"""Making text fit its box, because Slides will not do it for us.

**Verified against the live API:** `updateShapeProperties` with any autofit type
other than `NONE` is rejected — *"Autofit types other than NONE are not
supported."* Slides shrinks text to fit in its own editor and offers no way to
ask for that over the API. So the fit has to be computed here.

The failure this prevents was observed on a delivered flyer: a price reading
`$510,000` at 52.9pt in a 187.5pt box rendered clipped to `$510,00`. Every value
was present and correct, the API returned 200, and the flyer was wrong in the
one way a text-based check cannot see.

**How the width is worked out.** For the faces Carmen's designs actually draw
text in, `typemetrics.py` holds each character's measured advance width and the
answer is a sum rather than an estimate — accurate to a fraction of a point, so
`MEASURED_SAFETY` leaves only a token margin. Any other face falls back to the
five-class table below with the wider `SAFETY` margin: character width scales
with font size but not uniformly by character, and a single average is wrong
often enough to matter at flyer sizes.

The class table is a fallback, not a second opinion. It cannot separate a name
full of `w` and `k` from one full of `i` and `t`, and a flyer was delivered with
"Annie Nowicki" wrapped onto the Realtor title because of exactly that. The
rendered thumbnail in `pipeline/vision.py` remains the final gate either way.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

from gable.slides import typemetrics
from gable.slides.edit_common import MAX_FONT_PT, Request
from gable.slides.edits import set_font_size

#: EMU per point. Slides reports geometry in EMU and type in points.
EMU_PER_POINT: Final[float] = 12700.0

#: Width of one character as a fraction of the font size, by class. Measured
#: against the Corner House deck's own faces (EB Garamond and Open Sans) rather
#: than taken from a general table, because the headline face is narrow and a
#: generic 0.5 over-shrinks every title on every template.
WIDTH_FACTORS: Final[dict[str, float]] = {
    "wide": 0.82,  # M W @ %
    "upper": 0.62,  # A-Z
    "digit": 0.62,  # 0-9 and $ — bold display digits, see note below
    "lower": 0.50,  # a-z
    "narrow": 0.26,  # i l j . , ' : ; ! | space
}

#: Never shrink past this. Below it the text is unreadable on a printed flyer,
#: and the honest answer becomes "this does not fit" rather than a smaller lie.
MIN_READABLE_PT: Final[float] = 8.0

#: Leave real room, not a rounding allowance. These factors are a five-bucket
#: approximation of proportional type, and a rendered flyer proved the error is
#: not small: "$685,000" was estimated at 127.0pt inside a 132.2pt box — a pass
#: by 4% — and wrapped anyway, dropping the final zero onto a second line. The
#: heavy sans used for prices carries wider digits than the regular-weight
#: faces these numbers were derived from. Since the estimator can only ever be
#: approximate, the margin absorbs its error instead of pretending to precision.
#: Leave a little room rather than filling the box exactly to the pixel.
SAFETY: Final[float] = 0.90

#: The margin to leave when the face's real advance widths are known. Almost
#: none is needed: the design's own sample name measures 128.99pt in a 129.07pt
#: box and renders on one line, so the sum of advances is the wrap point to
#: within a rounding error. The 2% absorbs the pixel resolution the advances
#: were measured at, and kerning only ever makes a real line narrower.
#:
#: Keeping this separate from SAFETY matters: a 10% margin on a measured face
#: would shrink every name that fits perfectly well, and Carmen would see type
#: smaller than the design she drew.
MEASURED_SAFETY: Final[float] = 0.98


def _class_of(character: str) -> str:
    """Which width class a character belongs to."""
    if character in "MW@%":
        return "wide"
    if character in "ilj.,'\":;!| ":
        return "narrow"
    if character.isdigit() or character == "$":
        return "digit"
    if character.isupper():
        return "upper"
    return "lower"


#: Bold type is wider than the regular weight these factors were derived from.
#: Slides reports the weight, so this is read rather than guessed. Both strings
#: that overflowed a rendered flyer — the price and the agent name — were
#: weight 700 Open Sans, while the label beside them that fitted correctly was
#: weight 400.
#: # ASSUMPTION: 1.07 for Open Sans Bold against Regular. Measured against the
#: rendered flyer rather than from font tables; a design using a heavier display
#: cut would want its own figure.
BOLD_MULTIPLIER: Final[float] = 1.07

#: At or above this weight, treat the text as bold.
BOLD_WEIGHT: Final[int] = 600


def estimate_width_pt(
    text: str,
    font_size_pt: float,
    weight: int = 400,
    family: str = "",
) -> float:
    """Work out how wide a line of text renders.

    Args:
        text: The text. Only the widest line matters for overflow.
        font_size_pt: The font size in points.
        weight: Font weight, 400 regular and 700 bold. Bold renders wider.
        family: The font family. When it is one `typemetrics` has measured, the
            answer is a sum of real advance widths rather than an estimate.

    Returns:
        Width in points.

    Raises:
        Nothing.

    Note:
        The longest line is not the widest one — "Annie Nowicki" is shorter than
        "Louis Smith" is wide. Every line is measured and the widest returned.
    """
    lines = text.splitlines() or [""]
    exact = [typemetrics.advance_factor(line, family, weight) for line in lines]
    if family and all(factor is not None for factor in exact):
        return max(factor for factor in exact if factor is not None) * font_size_pt
    bold = BOLD_MULTIPLIER if weight >= BOLD_WEIGHT else 1.0
    return (
        max(sum(WIDTH_FACTORS[_class_of(c)] for c in line) for line in lines) * font_size_pt * bold
    )


@dataclass(frozen=True, slots=True)
class TextBox:
    """One text shape as read from a presentation."""

    object_id: str
    text: str
    font_size_pt: float
    width_emu: float
    #: How many lines the box can hold at its current height.
    lines: int = 1
    #: Font weight, 400 regular and 700 bold. Bold renders wider.
    weight: int = 400
    #: Font family, so a measured face is measured rather than estimated.
    family: str = ""


@dataclass(frozen=True, slots=True)
class Fit:
    """What a piece of text needs to sit inside its box."""

    object_id: str
    text: str
    current_pt: float
    box_width_pt: float
    fitted_pt: float

    @property
    def overflows(self) -> bool:
        """True when the text is currently wider than its box."""
        return self.fitted_pt < self.current_pt

    #: Font weight, carried through so preflight can measure needed width.
    weight: int = 400
    #: Exact proportional size needed before the readable floor is applied.
    #: Keeping it separate prevents an impossible 7-point fit from looking
    #: successful merely because the applied size is clamped to 8 points.
    required_pt: float | None = None

    @property
    def too_small_to_read(self) -> bool:
        """True when fitting it would make it unreadable.

        Worth surfacing rather than silently applying: at that point the honest
        answer is that the value does not belong in that box, which is a
        question for Carmen.
        """
        required = self.fitted_pt if self.required_pt is None else self.required_pt
        return required <= MIN_READABLE_PT or self.fitted_pt <= MIN_READABLE_PT


def wrapped_line_count(
    text: str,
    font_size_pt: float,
    usable_width_pt: float,
    weight: int = 400,
    family: str = "",
) -> int:
    """How many lines Slides will break this text into at this size.

    The width estimate alone is a ribbon: it asks whether the total advance
    width fits the box width times its line count, which assumes the text can
    be cut anywhere. Slides breaks at spaces. Donald Clark's "4812 Reisterstown
    Road, Baltimore, MD 21215" totals less than two 249-point lines and still
    needs three of them, because "4812 Reisterstown Road," on its own is wider
    than the box — so the ZIP landed on a third line, on top of the panel
    below. The ribbon said it fitted; the flyer said otherwise.

    Args:
        text: The text, with any explicit line breaks it already carries.
        font_size_pt: The size to measure at.
        usable_width_pt: One line's width, safety margin already applied.
        weight: Font weight, 400 regular and 700 bold.
        family: Font family, for measured advance widths.

    Returns:
        The number of rendered lines, never fewer than one. A single word wider
        than the box counts for as many lines as it needs, because Slides
        breaks inside a word rather than letting it overflow.

    Raises:
        Nothing.
    """
    if usable_width_pt <= 0:
        return 1
    total = 0
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            total += 1
            continue
        used = 0
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if estimate_width_pt(candidate, font_size_pt, weight, family) <= usable_width_pt:
                current = candidate
                continue
            if current:
                used += 1
            alone = estimate_width_pt(word, font_size_pt, weight, family)
            if alone > usable_width_pt:
                # Slides breaks inside a word rather than overflowing.
                used += math.ceil(alone / usable_width_pt) - 1
                current = ""
                used += 1
                continue
            current = word
        if current:
            used += 1
        total += max(1, used)
    return max(1, total)


def fit_for(
    object_id: str,
    text: str,
    current_pt: float,
    box_width_emu: float,
    lines: int = 1,
    weight: int = 400,
    family: str = "",
) -> Fit:
    """Work out the largest font size at which text fits its box.

    Args:
        object_id: The shape holding the text.
        text: What it now says.
        current_pt: Its current font size.
        box_width_emu: The shape's rendered width in EMU, scale already applied.
        lines: How many lines the box can hold. A two-line box fits roughly
            twice the text at the same size.
        weight: Font weight, 400 regular and 700 bold.
        family: Font family. A measured face gets a tight margin; an unmeasured
            one keeps the wider estimate margin.

    Returns:
        A `Fit`. `fitted_pt` equals `current_pt` when nothing needs to change.

    Raises:
        ValueError: if the box has no width, which means the caller passed an
            unscaled or missing transform.
    """
    if box_width_emu <= 0:
        msg = f"box width must be positive, got {box_width_emu}"
        raise ValueError(msg)

    margin = MEASURED_SAFETY if typemetrics.measured(family, weight) else SAFETY
    allowed = max(1, lines)
    usable_width_pt = (box_width_emu / EMU_PER_POINT) * margin
    box_width_pt = usable_width_pt * allowed
    needed = estimate_width_pt(text, current_pt, weight, family)
    wraps = wrapped_line_count(text, current_pt, usable_width_pt, weight, family)
    if needed <= box_width_pt and wraps <= allowed:
        return Fit(
            object_id,
            text,
            current_pt,
            box_width_pt,
            current_pt,
            weight,
            current_pt,
        )

    # Width scales linearly with font size, so the ratio gives the answer
    # directly rather than by searching.
    scaled = current_pt * (box_width_pt / needed) if needed > box_width_pt else current_pt
    # The ratio answers the ribbon question. Word wrapping is not linear, so the
    # result is then verified and stepped down until the text really does break
    # into the number of lines the box has. Five per cent a step converges in a
    # handful of passes and never below the readability floor, where the caller
    # takes over and reports the box as too small.
    while scaled > MIN_READABLE_PT and (
        wrapped_line_count(text, scaled, usable_width_pt, weight, family) > allowed
    ):
        scaled *= 0.95
    # Never below readable. The floor used to be MIN_FONT_PT, which is 1.0, and
    # a rendered flyer put an email address at 3.0pt and a phone number at 6.8pt
    # — legible in the API, invisible on the flyer. Chase's feedback on the first
    # reviewed flyer was exactly this: the number and email are too small.
    #
    # The design sized those boxes for the words "Phone" and "Email". A real
    # phone number and a real email are several times longer, so honouring the
    # box means destroying the type. Keeping the type readable and reporting the
    # box as too narrow is the honest trade; preflight reports the extra room
    # from the same width estimator.
    required = min(MAX_FONT_PT, scaled)
    # Round down: an upward rounding can make the supposedly fitted text wider
    # than the box again. Hundredth-point precision preserves a safe 8.04-point
    # fit instead of needlessly turning it into a human correction at 8 points.
    # If this actually reaches the boundary, the fit remains blocked.
    fitted = max(MIN_READABLE_PT, math.floor(required * 100) / 100)
    return Fit(object_id, text, current_pt, box_width_pt, fitted, weight, required)


def requests_for(fits: list[Fit]) -> list[Request]:
    """Turn fits into Slides requests, skipping the ones already fine.

    Args:
        fits: What each text box needs.

    Returns:
        `updateTextStyle` requests for the boxes that overflow. A box that
        already fits produces nothing, so a well-laid-out flyer costs no calls.

    Raises:
        EditError: if a computed size falls outside the allowed range.
    """
    out: list[Request] = []
    for fit in fits:
        if not fit.overflows or fit.too_small_to_read:
            continue
        out.extend(set_font_size(fit.object_id, fit.fitted_pt))
    return out


def plan_fits(
    elements: list[TextBox],
    dynamic: Collection[str] | None = None,
    single_line: Collection[str] | None = None,
) -> list[Fit]:
    """Work out what the text boxes carrying *this run's data* need.

    Args:
        elements: One `TextBox` per text shape on the slide.
        dynamic: The values this run inserted. When given, only boxes holding
            one of them are considered; every other box is the template's own
            copy and is left exactly as Carmen drew it. When None, every box is
            considered — the old behaviour, kept only for callers that have no
            way to say what they filled.
        single_line: Dynamic values that must remain on one line even when the
            source box is tall enough for two. Names, email addresses and phone
            numbers become misleading or broken when allowed to wrap.

    Returns:
        A `Fit` per considered element, in the order given. Elements missing a
        size or a width are skipped rather than guessed at.

    Raises:
        Nothing.

    Note:
        The filter exists because fitting every box rewrote the design. On the
        flyer reviewed 2026-08-11 it shrank "Just" from 140.8pt to 89.9pt and
        "Listed" from 109.4pt to 80.7pt — headline type that no submission
        touches — which pulled the two words visibly apart and left the address
        and price riding high in boxes built for larger text. The template is
        the specification; only the values change.
    """
    wanted = {value.strip() for value in dynamic if value.strip()} if dynamic is not None else None
    one_line = {value.strip() for value in single_line if value.strip()} if single_line else set()
    fits: list[Fit] = []
    for box in elements:
        if not box.text or box.font_size_pt <= 0 or box.width_emu <= 0:
            continue
        if wanted is not None and box.text.strip() not in wanted:
            continue
        fits.append(
            fit_for(
                box.object_id,
                box.text,
                box.font_size_pt,
                box.width_emu,
                # A break the designer typed is not wrapping. New Listing draws
                # its counts as "4\nBedrooms", and forcing that onto one line
                # shrank a two-line label until it was unreadable.
                max(1, box.text.strip().count("\n") + 1)
                if box.text.strip() in one_line
                else box.lines,
                box.weight,
                box.family,
            )
        )
    return fits
