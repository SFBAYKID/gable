"""Collapsing whitespace in a written name so two spellings can be compared.

Its own module for one reason: both `website.py` and `profile_page.py` need it,
and importing it from either would make those two import each other.

Deliberately does NOT strip punctuation. "Caleb Olawuyi, Realtor" keeps its
comma here — the edge-punctuation strip belongs to the page search, which has
its own reason for it, and applying it to every name would quietly change what
a workbook row is compared against.

Does not handle: deciding whether two names refer to the same person. That is
`website.names_agree`, which uses this and rather more besides.
"""

from __future__ import annotations


def clean_name(value: str) -> str:
    """Collapse whitespace without changing any submitted spelling.

    Args:
        value: A name as written on a form, a page title, or a workbook row.

    Returns:
        The same name with runs of whitespace reduced to single spaces and the
        ends trimmed. Never reorders, re-cases, or rewrites the words.

    Raises:
        Nothing.
    """
    return " ".join(value.split())
