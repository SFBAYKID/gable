"""Comparing spreadsheet headers, which is how every tab and workbook is read.

Column *positions* are never assumed anywhere in Gable, because they have moved
twice in one day: the form split its agent name across two columns and shifted
everything after it, and the roster's header row moved from row 2 to row 1. Both
were silent — the first read "Instagram Story" as a property address, the second
stored nobody at all — so the rule is to find a column by its name.

Only cosmetic differences are absorbed. `"Agent Email "`, `"agent email"` and
`"Agent  Email"` are the same header; a genuinely different one is reported by
the caller rather than guessed at.
"""

from __future__ import annotations

import re
from typing import Final

_HORIZONTAL_WS: Final[re.Pattern[str]] = re.compile(r"[^\S\n]+")


def fold_header(header: str) -> str:
    """Reduce a header to its match key: casefolded, whitespace-collapsed.

    Args:
        header: A header cell exactly as the sheet returns it, which may carry
            a newline where the form's question wrapped.

    Returns:
        The comparable form.

    Raises:
        Nothing.
    """
    return _HORIZONTAL_WS.sub(" ", header.replace("\n", " ")).strip().casefold()
