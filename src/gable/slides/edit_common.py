"""Shared vocabulary for the edit tools: request type, guards, colour parsing.

Extracted so `edits.py` and `geometry.py` share one definition of what a colour
is and what an unusable target looks like, rather than drifting apart.
"""

from __future__ import annotations

import re
from typing import Any, Final

#: A Slides API request. The shape is a large open union defined by Google, so a
#: precise TypedDict would be dozens of members Google can extend at any time.
#: `Any` is the honest annotation.
Request = dict[str, Any]

#: Type is measured in points, because that is what a human means by "font size".
MIN_FONT_PT: Final[float] = 1.0
MAX_FONT_PT: Final[float] = 400.0

#: Scale guards. A 0x scale makes an element vanish in a way that reads as data
#: loss; past 10x is almost certainly a misparsed instruction.
MIN_SCALE: Final[float] = 0.05
MAX_SCALE: Final[float] = 10.0

_HEX6: Final[re.Pattern[str]] = re.compile(r"^#?([0-9a-fA-F]{6})$")
_HEX3: Final[re.Pattern[str]] = re.compile(r"^#?([0-9a-fA-F]{3})$")

#: Names Carmen is likely to say, mapped to hex. Keeps "make it black" working
#: without the model having to invent a hex code.
NAMED_COLOURS: Final[dict[str, str]] = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "navy": "#1B2A4A",
    "grey": "#808080",
    "gray": "#808080",
    "green": "#2E7D32",
    "blue": "#1565C0",
    "gold": "#B8860B",
    "cream": "#F5F0E6",
}


class EditError(Exception):
    """Raised when an edit request cannot be built safely."""


def parse_colour(value: str) -> dict[str, float]:
    """Turn `#RRGGBB`, `#RGB`, or a colour name into a Slides `rgbColor`.

    Args:
        value: `#1B2A4A`, `1B2A4A`, `#ABC`, or a name from `NAMED_COLOURS`.

    Returns:
        `{"red": float, "green": float, "blue": float}`, each in 0.0-1.0.
        Slides wants floats, not 0-255 and not hex; sending the wrong scale
        renders everything black.

    Raises:
        EditError: if the value is neither a known name nor valid hex. The
            message lists the names, because the usual cause is a colour nobody
            has mapped yet.
    """
    key = value.strip().lower()
    if key in NAMED_COLOURS:
        value = NAMED_COLOURS[key]
    stripped = value.strip()
    if match := _HEX6.match(stripped):
        digits = match.group(1)
    elif short := _HEX3.match(stripped):
        # A model will happily emit #ABC; expand rather than refuse.
        digits = "".join(c * 2 for c in short.group(1))
    else:
        known = ", ".join(sorted(NAMED_COLOURS))
        msg = f"cannot read colour {value!r}; use #RRGGBB or one of: {known}"
        raise EditError(msg)
    return {
        "red": int(digits[0:2], 16) / 255.0,
        "green": int(digits[2:4], 16) / 255.0,
        "blue": int(digits[4:6], 16) / 255.0,
    }


def require_object_id(object_id: str) -> None:
    """Reject an empty target before it becomes an opaque Google error.

    Args:
        object_id: The element id a tool was asked to act on.

    Raises:
        EditError: if it is empty or whitespace.
    """
    if not object_id or not object_id.strip():
        msg = "object_id is required; nothing identifies which element to change"
        raise EditError(msg)
