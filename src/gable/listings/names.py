"""Capitalising a person's name for print.

The roster carries `Eric jacobs`, `Julie mayer` and `Kirby-jay john`, typed at
speed into a form. Each of those renders on a flyer under the agent's own
photograph, which makes a lowercase surname worse than untidy — it looks like
the brokerage does not know its own people.

`str.title()` cannot do this. It produces `Mcdonald`, `O'Brien` becomes
`O'Brien` only by luck, `Kirby-Jay` becomes `Kirby-Jay` but `d'Angelo` becomes
`D'Angelo`, and `III` becomes `Iii`. So the cases are handled explicitly.

As with an address, this changes presentation and nothing else. A name is not
corrected, expanded, reordered or abbreviated: `Bobby` never becomes `Robert`,
because the person chose what to be called and a flyer is not the place to
argue with them.

Does not handle: deciding which of several spellings on the roster is the real
one. That is a question for Carmen, not a rule.
"""

from __future__ import annotations

import re
from typing import Final

#: Generational suffixes, which are upper case or capitalised, never title cased.
#: `str.title()` renders "III" as "Iii".
SUFFIXES: Final[dict[str, str]] = {
    "jr": "Jr.",
    "jr.": "Jr.",
    "sr": "Sr.",
    "sr.": "Sr.",
    "ii": "II",
    "iii": "III",
    "iv": "IV",
    "v": "V",
}

#: Particles that stay lower case inside a surname: `Piet de Dreu` is on the
#: roster and `Piet De Dreu` is wrong.
PARTICLES: Final[frozenset[str]] = frozenset(
    {"de", "del", "della", "der", "di", "du", "la", "le", "van", "von", "ter", "bin", "al"}
)

_MC: Final[re.Pattern[str]] = re.compile(r"^(mc|mac)([a-z]{2,})$", re.IGNORECASE)
_APOSTROPHE: Final[re.Pattern[str]] = re.compile(r"^([a-z]')([a-z].*)$", re.IGNORECASE)


def _part(word: str) -> str:
    """Capitalise one name component.

    Args:
        word: A single component, already split on spaces.

    Returns:
        The component cased for print, with `Mc`, `Mac`, apostrophes, hyphens
        and generational suffixes each handled explicitly.

    Raises:
        Nothing.
    """
    if not word:
        return word
    # A capital anywhere but the front is authorial: DePinto, DeShawn, LaTonya.
    # Recasing those destroys a spelling the person chose. All-caps input is not
    # authorial — it is a caps-lock key — so it is still recased.
    if not word.isupper() and any(c.isupper() for c in word[1:]):
        return word
    lowered = word.lower()
    if lowered in SUFFIXES:
        return SUFFIXES[lowered]
    if "-" in word:
        return "-".join(_part(piece) for piece in word.split("-"))
    matched = _MC.match(word)
    if matched:
        prefix, rest = matched.groups()
        return prefix.capitalize() + rest.capitalize()
    apostrophe = _APOSTROPHE.match(word)
    if apostrophe:
        head, tail = apostrophe.groups()
        return head.capitalize() + tail.capitalize()
    return word.capitalize()


def tidy_name(name: str) -> str:
    """Capitalise a person's name for a flyer.

    Args:
        name: The name as it appears on the roster or in a submission.

    Returns:
        The same name, cased for print. Particles such as `de` and `van` stay
        lower case when they sit inside the name; a name that is only a particle
        is capitalised, because then it is the name.

    Raises:
        Nothing. Empty input returns empty, which the caller should treat as a
        missing name rather than an error here.

    Note:
        Presentation only. Nothing is corrected, expanded, reordered or
        abbreviated — `Bobby` is never promoted to `Robert`, because the person
        chose what to be called.
    """
    if not name or not name.strip():
        return ""
    words = " ".join(name.split()).split(" ")
    out: list[str] = []
    for index, word in enumerate(words):
        # A particle stays lower case whenever something follows it. It may lead
        # the field: first and last names are stored separately, so "de Dreu"
        # arrives as a surname on its own and "De Dreu" is still wrong.
        if word.lower() in PARTICLES and index < len(words) - 1:
            out.append(word.lower())
            continue
        out.append(_part(word))
    return " ".join(out)
