"""Making text fit its box, because Slides will not do it for us.

**Verified against the live API:** `updateShapeProperties` with any autofit type
other than `NONE` is rejected — *"Autofit types other than NONE are not
supported."* Slides shrinks text to fit in its own editor and offers no way to
ask for that over the API. So the fit has to be computed here.

The failure this prevents was observed on a delivered flyer: a price reading
`$510,000` at 52.9pt in a 187.5pt box rendered clipped to `$510,00`. Every value
was present and correct, the API returned 200, and the flyer was wrong in the
one way a text-based check cannot see.

**How the estimate works.** Character width scales with font size, but not
uniformly by character: a capital W is roughly three times an l. A single average
is wrong often enough to matter at flyer sizes, so this uses a small width table
by character class. It is an estimate — the honest test is the rendered
thumbnail, which is what `pipeline/vision.py` is for — but it is a good enough
estimate to stop the clipping, and it costs nothing.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

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


def estimate_width_pt(text: str, font_size_pt: float, weight: int = 400) -> float:
    """Estimate how wide a line of text renders.

    Args:
        text: The text. Only the longest line matters for overflow.
        font_size_pt: The font size in points.
        weight: Font weight, 400 regular and 700 bold. Bold renders wider.

    Returns:
        Estimated width in points.

    Raises:
        Nothing.
    """
    longest = max(text.splitlines() or [""], key=len, default="")
    bold = BOLD_MULTIPLIER if weight >= BOLD_WEIGHT else 1.0
    return sum(WIDTH_FACTORS[_class_of(c)] for c in longest) * font_size_pt * bold


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

    @property
    def too_small_to_read(self) -> bool:
        """True when fitting it would make it unreadable.

        Worth surfacing rather than silently applying: at that point the honest
        answer is that the value does not belong in that box, which is a
        question for Carmen.
        """
        return self.fitted_pt <= MIN_READABLE_PT


def fit_for(
    object_id: str,
    text: str,
    current_pt: float,
    box_width_emu: float,
    lines: int = 1,
    weight: int = 400,
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

    Returns:
        A `Fit`. `fitted_pt` equals `current_pt` when nothing needs to change.

    Raises:
        ValueError: if the box has no width, which means the caller passed an
            unscaled or missing transform.
    """
    if box_width_emu <= 0:
        msg = f"box width must be positive, got {box_width_emu}"
        raise ValueError(msg)

    box_width_pt = (box_width_emu / EMU_PER_POINT) * max(1, lines) * SAFETY
    needed = estimate_width_pt(text, current_pt, weight)
    if needed <= box_width_pt:
        return Fit(object_id, text, current_pt, box_width_pt, current_pt, weight)

    # Width scales linearly with font size, so the ratio gives the answer
    # directly rather than by searching.
    scaled = current_pt * (box_width_pt / needed)
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
    fitted = max(MIN_READABLE_PT, min(MAX_FONT_PT, round(scaled, 1)))
    return Fit(object_id, text, current_pt, box_width_pt, fitted, weight)


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
        if not fit.overflows:
            continue
        out.extend(set_font_size(fit.object_id, fit.fitted_pt))
    return out


def plan_fits(elements: list[TextBox], dynamic: Collection[str] | None = None) -> list[Fit]:
    """Work out what the text boxes carrying *this run's data* need.

    Args:
        elements: One `TextBox` per text shape on the slide.
        dynamic: The values this run inserted. When given, only boxes holding
            one of them are considered; every other box is the template's own
            copy and is left exactly as Carmen drew it. When None, every box is
            considered — the old behaviour, kept only for callers that have no
            way to say what they filled.

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
                box.lines,
                box.weight,
            )
        )
    return fits
