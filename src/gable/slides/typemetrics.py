"""Measured advance widths for the faces Carmen's designs actually use.

Slides will not fit text for us — `updateShapeProperties` rejects every autofit
type but `NONE` — so `fitting.py` has to predict where a line breaks. It used to
predict from a five-class character table, which averages every lowercase letter
to one width and so cannot separate a name full of `w` and `k` from one full of
`i` and `t`. On a delivered flyer it put "Annie Nowicki" at 116.15pt inside a
129.07pt box; Slides wrapped it, and the second line landed on top of the
Realtor title beneath. A 14 percent underestimate is not something a 10 percent
safety margin can absorb.

These numbers are measurements, not estimates. Each was taken by writing one
character repeated N and 2N times into a real text box, rendering both, and
subtracting: the difference cancels side bearings exactly and leaves the advance
width as a fraction of the font size. Verified against three independent
observations on the Open House design's 129.07pt name box:

* "Louis Smith" at 22.12pt sums to 128.99pt — the design's own sample, which
  renders on one line with 0.08pt to spare.
* "Annie Nowicki" at 18.78pt sums to 135.16pt, and Slides wraps it.
* "Annie Nowicki" at 17.50pt sums to 125.95pt, and Slides does not.

The usable width is the box width; these designs carry no text inset.

Does not handle: kerning pairs, which make a real line marginally narrower than
the sum of its advances, so the sum errs in the direction that is safe. A face
outside this table falls back to `fitting`'s class estimate and its wider safety
margin — see `measured`.
"""

from __future__ import annotations

from typing import Final

#: Weights collapse to the two the designs use. Anything at or above this is
#: measured as bold.
BOLD_WEIGHT: Final[int] = 600

#: Advance width per character as a fraction of the font size, stored as a
#: character string and a matching list of numbers so the table stays readable
#: at a glance and short enough to live beside the code that uses it. Measured
#: 2026-08-14 against rendered Slides output; the module docstring gives the
#: method. Characters absent from a face were measured as zero-width — only EB
#: Garamond's underscore, which draws below the sampled band — and are dropped
#: rather than recorded wrong.
_MEASURED: Final[dict[str, tuple[str, str]]] = {
    "open sans|400": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz01234567"
        "89 .,'\"-/&#$()+:;!?@%®™’‘“”–—|*_=[]{}<>~^`\\",
        "0.6323,0.6455,0.6279,0.7245,0.5533,0.5181,0.7289,0.7377,0.281,0.2679,0.6148,"
        "0.5181,0.9002,0.7509,0.7772,0.6016,0.7772,0.6191,0.5489,0.5533,0.7333,0.5972"
        ",0.9221,0.5796,0.5621,0.5708,0.5577,0.6104,0.4786,0.6104,0.5621,0.3337,0.540"
        "1,0.6104,0.2547,0.2547,0.5269,0.2547,0.9221,0.6148,0.6016,0.6104,0.6104,0.40"
        "84,0.4742,0.3557,0.6148,0.4962,0.7772,0.5225,0.5006,0.4698,0.5752,0.5708,0.5"
        "752,0.5752,0.5708,0.5752,0.5708,0.5752,0.5752,0.5752,0.2591,0.2635,0.2547,0."
        "2196,0.3952,0.3206,0.3689,0.7289,0.6455,0.5752,0.2986,0.2942,0.5752,0.2635,0"
        ".2635,0.2635,0.4303,0.9002,0.8255,0.8299,0.7641,0.1669,0.1669,0.3513,0.3513,"
        "0.5006,1.0012,0.5489,0.5489,0.4391,0.5752,0.3293,0.3293,0.3732,0.3776,0.5752"
        ",0.5708,0.5752,0.5752,0.2766,0.3689",
    ),
    "open sans|700": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz01234567"
        "89 .,'\"-/&#$()+:;!?@%®™’‘“”–—|*_=[]{}<>~^`\\",
        "0.6894,0.6718,0.6411,0.7377,0.5577,0.5489,0.7245,0.7641,0.3293,0.3293,0.6631"
        ",0.5665,0.9441,0.8124,0.7992,0.6279,0.7992,0.6587,0.5489,0.5796,0.7553,0.649"
        "9,0.966,0.6674,0.6235,0.5796,0.606,0.6323,0.5138,0.6323,0.5884,0.3864,0.5621"
        ",0.6587,0.3074,0.3074,0.6191,0.3074,0.9792,0.6587,0.6191,0.6323,0.6323,0.456"
        "7,0.4962,0.4303,0.6587,0.5665,0.8563,0.5796,0.5665,0.4874,0.5708,0.5708,0.57"
        "08,0.5708,0.5708,0.5708,0.5708,0.5708,0.5708,0.5708,0.2591,0.2854,0.2854,0.2"
        "679,0.4698,0.3206,0.4128,0.7509,0.6455,0.5708,0.3425,0.3381,0.5708,0.2854,0."
        "2854,0.2854,0.4786,0.8958,0.9002,0.8299,0.7728,0.2196,0.2196,0.4479,0.4479,0"
        ".5006,1.0012,0.5533,0.5445,0.4128,0.5708,0.3337,0.3337,0.3952,0.3952,0.5708,"
        "0.5708,0.5708,0.5708,0.3601,0.4128",
    ),
    "eb garamond|400": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz01234567"
        "89 .,'\"-/&#$()+:;!?@%®™’‘“”–—|*=[]{}<>~^`\\",
        "0.6938,0.5884,0.7114,0.7553,0.5621,0.5138,0.7289,0.808,0.3381,0.3381,0.6806,"
        "0.584,0.9002,0.7904,0.7641,0.5533,0.7641,0.7114,0.4655,0.6674,0.7377,0.6718,"
        "0.9133,0.707,0.5796,0.606,0.3996,0.5138,0.3864,0.505,0.382,0.2898,0.4347,0.5"
        "181,0.2459,0.2239,0.4655,0.2415,0.7816,0.5269,0.4962,0.5181,0.5225,0.3337,0."
        "3249,0.3162,0.5269,0.4347,0.685,0.4303,0.4391,0.3776,0.4786,0.4786,0.483,0.4"
        "786,0.483,0.483,0.483,0.483,0.4786,0.483,0.202,0.2327,0.2283,0.1976,0.3293,0"
        ".2722,0.3996,0.7597,0.4655,0.4391,0.3162,0.3162,0.5884,0.2503,0.2283,0.2503,"
        "0.3776,0.7509,0.6499,0.6631,1.0626,0.2415,0.2415,0.4172,0.4172,0.5533,0.9529"
        ",0.2591,0.3381,0.5665,0.3337,0.3293,0.3776,0.3732,0.5577,0.5577,0.5006,0.500"
        "6,0.202,0.3952",
    ),
    "eb garamond|700": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz01234567"
        "89 .,'\"-/&#$()+:;!?@%®™’‘“”–—|*=[]{}<>~^`\\",
        "0.7201,0.6279,0.707,0.7684,0.5752,0.5489,0.7201,0.7992,0.3645,0.3689,0.7641,"
        "0.584,0.9177,0.808,0.7816,0.606,0.7816,0.7597,0.4918,0.6938,0.7465,0.707,1.0"
        "231,0.7465,0.6543,0.5928,0.4391,0.5181,0.404,0.5357,0.4172,0.3293,0.5006,0.5"
        "577,0.2942,0.2635,0.5489,0.2854,0.808,0.5665,0.505,0.5533,0.5357,0.4128,0.36"
        "01,0.3776,0.5533,0.483,0.7509,0.5006,0.483,0.4391,0.5313,0.5269,0.5313,0.526"
        "9,0.5269,0.5313,0.5313,0.5313,0.5269,0.5313,0.2371,0.2459,0.2459,0.2371,0.39"
        "52,0.3162,0.4084,0.8036,0.483,0.4611,0.303,0.303,0.6016,0.281,0.2459,0.2898,"
        "0.3952,0.7816,0.7026,0.685,1.1768,0.2415,0.2371,0.4391,0.4391,0.6323,0.9617,"
        "0.2722,0.3557,0.6323,0.3557,0.3557,0.3732,0.3732,0.5401,0.5401,0.5489,0.5138"
        ",0.2371,0.4084",
    ),
    "raleway|400": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz01234567"
        "89 .,'\"-/&#$()+:;!?@%®™’‘“”–—|*_=[]{}<>~^`\\",
        "0.6762,0.6674,0.6762,0.7157,0.6104,0.584,0.7157,0.7377,0.2503,0.4698,0.6411,"
        "0.584,0.8782,0.7728,0.7465,0.6235,0.7509,0.6543,0.606,0.606,0.7553,0.6806,1."
        "0451,0.6323,0.6499,0.6279,0.5445,0.6148,0.5445,0.6191,0.5884,0.2898,0.6148,0"
        ".5796,0.2196,0.2459,0.5313,0.2722,0.9177,0.5796,0.5972,0.6148,0.6191,0.3513,"
        "0.483,0.3337,0.5972,0.5313,0.8124,0.505,0.5401,0.4918,0.6104,0.4303,0.5181,0"
        ".5357,0.5445,0.5445,0.6016,0.5313,0.5928,0.584,0.2547,0.1888,0.2239,0.1976,0"
        ".2986,0.4215,0.3469,0.6674,0.6806,0.6191,0.2591,0.2591,0.4128,0.1888,0.202,0"
        ".2503,0.4742,0.8387,0.7157,0.8299,0.7289,0.1888,0.1932,0.3162,0.3118,0.5708,"
        "0.9002,0.2327,0.3206,0.5138,0.4347,0.2591,0.2591,0.2591,0.2591,0.5094,0.5094"
        ",0.5094,0.5401,0.2196,0.5665",
    ),
    "raleway|700": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz01234567"
        "89 .,'\"-/&#$()+:;!?@%®™’‘“”–—|*_=[]{}<>~^`\\",
        "0.6718,0.6762,0.6762,0.7157,0.6016,0.5708,0.7289,0.7465,0.2854,0.4786,0.6718"
        ",0.584,0.865,0.7597,0.7553,0.6235,0.7553,0.6631,0.6148,0.6191,0.7509,0.6762,"
        "1.0539,0.6455,0.6587,0.6235,0.5752,0.6323,0.5577,0.6411,0.6016,0.3206,0.6323"
        ",0.6016,0.2547,0.2942,0.5577,0.3162,0.9309,0.6016,0.606,0.6323,0.6323,0.3864"
        ",0.4962,0.382,0.6191,0.5489,0.8387,0.5269,0.5489,0.4918,0.6148,0.4962,0.5621"
        ",0.5577,0.5708,0.5533,0.606,0.5665,0.606,0.5884,0.2371,0.2239,0.2327,0.2327,"
        "0.3908,0.4215,0.4215,0.7114,0.7201,0.6323,0.3074,0.303,0.4215,0.2503,0.2591,"
        "0.3118,0.4874,0.8431,0.7816,0.8387,0.8299,0.2371,0.2459,0.4347,0.4259,0.606,"
        "0.9265,0.2722,0.3425,0.4786,0.4347,0.3162,0.3162,0.3162,0.3162,0.5181,0.5138"
        ",0.5665,0.5884,0.2547,0.6367",
    ),
}

CHAR_ADVANCE: Final[dict[str, dict[str, float]]] = {
    face: dict(zip(characters, (float(v) for v in widths.split(",")), strict=True))
    for face, (characters, widths) in _MEASURED.items()
}


def _key(family: str, weight: int) -> str:
    """The table key for one face, normalised the way the API reports it."""
    return f"{' '.join(family.split()).casefold()}|{700 if weight >= BOLD_WEIGHT else 400}"


def measured(family: str, weight: int) -> bool:
    """Whether this face's real advance widths are known.

    Args:
        family: The font family, as `weightedFontFamily.fontFamily` reports it.
        weight: The font weight, 400 or 700.

    Returns:
        True when `advance_factor` will return a measurement rather than None.
        Callers use this to choose how much safety margin to leave: a measured
        face needs almost none, an estimated one needs real room.

    Raises:
        Nothing.
    """
    return _key(family, weight) in CHAR_ADVANCE


def advance_factor(text: str, family: str, weight: int) -> float | None:
    """Total advance width of one line, as a multiple of the font size.

    Args:
        text: One line. Callers split lines before asking.
        family: The font family.
        weight: The font weight, 400 or 700.

    Returns:
        The summed advance, or None when the face has not been measured. A
        character outside the measured set falls back to the widest character in
        that face, which overstates rather than understates the line.

    Raises:
        Nothing.
    """
    table = CHAR_ADVANCE.get(_key(family, weight))
    if table is None:
        return None
    widest = max(table.values())
    return sum(table.get(character, widest) for character in text)
