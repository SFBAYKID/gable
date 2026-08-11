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

from dataclasses import dataclass
from typing import Final

from gable.slides.edit_common import MAX_FONT_PT, MIN_FONT_PT, Request
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
    "digit": 0.55,  # 0-9 and $
    "lower": 0.50,  # a-z
    "narrow": 0.26,  # i l j . , ' : ; ! | space
}

#: Never shrink past this. Below it the text is unreadable on a printed flyer,
#: and the honest answer becomes "this does not fit" rather than a smaller lie.
MIN_READABLE_PT: Final[float] = 8.0

#: Leave a little room rather than filling the box exactly to the pixel.
SAFETY: Final[float] = 0.96


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


def estimate_width_pt(text: str, font_size_pt: float) -> float:
    """Estimate how wide a line of text renders.

    Args:
        text: The text. Only the longest line matters for overflow.
        font_size_pt: The font size in points.

    Returns:
        Estimated width in points.

    Raises:
        Nothing.
    """
    longest = max(text.splitlines() or [""], key=len, default="")
    return sum(WIDTH_FACTORS[_class_of(c)] for c in longest) * font_size_pt


@dataclass(frozen=True, slots=True)
class TextBox:
    """One text shape as read from a presentation."""

    object_id: str
    text: str
    font_size_pt: float
    width_emu: float
    #: How many lines the box can hold at its current height.
    lines: int = 1


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

    @property
    def too_small_to_read(self) -> bool:
        """True when fitting it would make it unreadable.

        Worth surfacing rather than silently applying: at that point the honest
        answer is that the value does not belong in that box, which is a
        question for Carmen.
        """
        return self.fitted_pt <= MIN_READABLE_PT


def fit_for(
    object_id: str, text: str, current_pt: float, box_width_emu: float, lines: int = 1
) -> Fit:
    """Work out the largest font size at which text fits its box.

    Args:
        object_id: The shape holding the text.
        text: What it now says.
        current_pt: Its current font size.
        box_width_emu: The shape's rendered width in EMU, scale already applied.
        lines: How many lines the box can hold. A two-line box fits roughly
            twice the text at the same size.

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
    needed = estimate_width_pt(text, current_pt)
    if needed <= box_width_pt:
        return Fit(object_id, text, current_pt, box_width_pt, current_pt)

    # Width scales linearly with font size, so the ratio gives the answer
    # directly rather than by searching.
    scaled = current_pt * (box_width_pt / needed)
    fitted = max(MIN_FONT_PT, min(MAX_FONT_PT, round(scaled, 1)))
    return Fit(object_id, text, current_pt, box_width_pt, fitted)


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


def plan_fits(elements: list[TextBox]) -> list[Fit]:
    """Work out what every text box on a slide needs.

    Args:
        elements: One `TextBox` per text shape on the slide.

    Returns:
        A `Fit` per element, in the order given. Elements missing a size or a
        width are skipped rather than guessed at.

    Raises:
        Nothing.
    """
    fits: list[Fit] = []
    for box in elements:
        if not box.text or box.font_size_pt <= 0 or box.width_emu <= 0:
            continue
        fits.append(fit_for(box.object_id, box.text, box.font_size_pt, box.width_emu, box.lines))
    return fits
