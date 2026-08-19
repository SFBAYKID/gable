"""Reading the counts an agent already wrote into their own post details.

The form's "Include details for post" field is free text, and agents use it to
describe the listing in their own words. On 2026-08-19 both requests for 1921
Lincoln Ave carried `3Bed/2 Bath` there, and both flyers went out stating a
bathroom count that came from the design's sample content instead — three on
one, five on the other. Gable had the right answer in hand and never looked at
it.

So this reads only what the agent stated about their own property, and only
the three plain measurements. It does **not** read the price: a list price, a
Sold closing price and a Price Reduction's new price are different values with
different rules in `intake`, and quietly taking a number out of prose would
cut across them.

Nothing here infers. A count appears or it does not, and a field the text
states twice with different numbers is treated as unstated rather than
guessed — see `_single`.
"""

from __future__ import annotations

import re
from typing import Final

#: "3Bed", "3 bedrooms", "3 bd", "3BR". The digits must lead, so "2nd Kitchen"
#: and "Backyard Oasis" cannot match.
_BEDS: Final[re.Pattern[str]] = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:bed(?:room)?s?|bds?|brs?)\b", re.IGNORECASE
)

#: "2 Bath", "2.5 baths", "2 ba". Halves are ordinary in a bathroom count.
_BATHS: Final[re.Pattern[str]] = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:bath(?:room)?s?|bas?)\b", re.IGNORECASE
)

#: "1,880 sqft", "1880 sq ft", "1,880 SF".
_SQFT: Final[re.Pattern[str]] = re.compile(
    r"([\d,]+)\s*(?:sq\.?\s*(?:ft|feet)\b|sqft\b|sf\b)", re.IGNORECASE
)


def _single(pattern: re.Pattern[str], text: str) -> str:
    """Return the one value this pattern finds, or "" if it finds none or many.

    Two different counts in one description is a person describing something
    Gable cannot resolve — a main house and a guest suite, say. Choosing
    between them would put a number on a client's flyer that nobody wrote.

    Args:
        pattern: One measurement pattern.
        text: The agent's own description.

    Returns:
        The matched number as written, without its unit, or an empty string.

    Raises:
        Nothing.
    """
    found = {match.group(1).strip().lstrip("0") or "0" for match in pattern.finditer(text)}
    return found.pop() if len(found) == 1 else ""


def counts_in(text: str) -> dict[str, str]:
    """Read bedrooms, bathrooms and square footage out of an agent's own words.

    Args:
        text: The post-details field, or any free text the agent supplied.

    Returns:
        A mapping with `beds`, `baths` and `square_feet` for whichever were
        stated exactly once. Absent keys mean the text did not say.

    Raises:
        Nothing.
    """
    if not text or not text.strip():
        return {}
    found = {
        "beds": _single(_BEDS, text),
        "baths": _single(_BATHS, text),
        "square_feet": _single(_SQFT, text),
    }
    return {field: value for field, value in found.items() if value}
