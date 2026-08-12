"""Tidying a property address without changing what it says.

Agents type addresses into a form at speed, and the form takes whatever they
type. Real submissions include `1225 canberwell rd baltimore md 21228`,
`4265 bright bay way Ellicott city 21042` and `10205 Douglas Ave, Silver
Spring, MD, 20902`. Each is understandable and none is presentable: this text
is set at 44pt across the middle of a flyer that goes to a client.

So the rules here are deliberately conservative. Capitalisation, punctuation and
spacing are fixed; **nothing is added, removed, corrected or reordered**. A
misspelled street stays misspelled and a missing state stays missing, because
inventing either would put a different address on a flyer for a real property —
which is worse than an untidy one, and invisible to anyone checking.

Does not handle: validating that the address exists, expanding or abbreviating
street types, or inferring a state from a city. Those all require knowing
something the submission did not say.
"""

from __future__ import annotations

import re
from typing import Final

#: The fifty states plus DC, as postal codes. Used to recognise a state token so
#: it can be capitalised and given its comma — never to insert one.
STATE_CODES: Final[frozenset[str]] = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "DC",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
    ]
)

#: Compass directions stay upper case. Title casing turns "NW" into "Nw", which
#: reads as a typo on a flyer.
DIRECTIONS: Final[frozenset[str]] = frozenset({"N", "S", "E", "W", "NE", "NW", "SE", "SW"})

#: Words that stay lower case inside a name, as house style. "Havre De Grace"
#: is a real Maryland town and reads wrong; "Havre de Grace" is right.
MINOR_WORDS: Final[frozenset[str]] = frozenset(
    {"de", "of", "the", "at", "on", "upon", "van", "von"}
)

#: Unit markers whose following token is an identifier, not a word: the "D" in
#: "# D" and the "118" in "Unit 118" must not be title cased into prose.
UNIT_MARKERS: Final[frozenset[str]] = frozenset(
    {"#", "apt", "apt.", "unit", "ste", "ste.", "suite"}
)

#: Street types, used only to find where the street ends and the city begins so
#: a comma can go between them. Never used to rewrite the type itself: an agent
#: who typed "Avenue" gets "Avenue", not "Ave".
STREET_TYPES: Final[frozenset[str]] = frozenset(
    [
        "st",
        "street",
        "ave",
        "avenue",
        "rd",
        "road",
        "dr",
        "drive",
        "ln",
        "lane",
        "way",
        "blvd",
        "boulevard",
        "ct",
        "court",
        "pl",
        "place",
        "ter",
        "terrace",
        "cir",
        "circle",
        "pkwy",
        "parkway",
        "hwy",
        "highway",
        "trl",
        "trail",
        "loop",
        "row",
        "sq",
        "square",
        "walk",
        "path",
        "run",
        "pike",
    ]
)

_ORDINAL: Final[re.Pattern[str]] = re.compile(r"^\d+(st|nd|rd|th)$", re.IGNORECASE)
_ZIP: Final[re.Pattern[str]] = re.compile(r"^\d{5}(-\d{4})?$")
_MC: Final[re.Pattern[str]] = re.compile(r"^(mc|mac)([a-z]{2,})$", re.IGNORECASE)


def _word(token: str) -> str:
    """Capitalise one token according to what it is.

    Args:
        token: A single whitespace-delimited token, punctuation attached.

    Returns:
        The token cased for display. Numbers, ordinals and postcodes are left
        alone; directions and state codes are upper cased; `McCarthy` keeps its
        inner capital, which `str.title` destroys.

    Raises:
        Nothing.
    """
    if not token:
        return token
    core = token.strip(",.")
    trailing = token[len(core.rstrip()) :] if token.endswith(",") else ""
    if not core:
        return token

    upper = core.upper()
    if upper in DIRECTIONS or upper in STATE_CODES:
        return upper + trailing
    if _ZIP.match(core) or core.isdigit():
        return core + trailing
    if _ORDINAL.match(core):
        return core.lower() + trailing
    matched = _MC.match(core)
    if matched:
        prefix, rest = matched.groups()
        return prefix.capitalize() + rest.capitalize() + trailing
    if "-" in core:
        return "-".join(part.capitalize() for part in core.split("-")) + trailing
    return core.capitalize() + trailing


def tidy(address: str) -> str:
    """Present a submitted address properly, without altering what it says.

    Args:
        address: The address exactly as the agent typed it.

    Returns:
        The same address with sane capitalisation, one space between tokens, a
        single comma before the state, and no trailing or doubled punctuation.
        An address with no recognisable state is returned tidied but otherwise
        untouched — a missing state is the submission's, not ours to supply.

    Raises:
        Nothing. An empty or whitespace-only input comes back empty, which the
        caller should treat as a missing address rather than an error here.

    Note:
        Nothing is added, removed or reordered. A street name spelled wrong on
        the form stays wrong on the flyer, because the alternative is guessing
        at a real property's address and being confidently mistaken.
    """
    if not address or not address.strip():
        return ""

    # Normalise separators first so tokens are clean, then rebuild the commas
    # we actually want. Submissions arrive with none, with doubles, and with
    # commas floating between spaces.
    collapsed = re.sub(r"\s*,\s*", ", ", " ".join(address.split()))
    collapsed = re.sub(r"(,\s*)+", ", ", collapsed).strip().strip(",").strip()

    tokens = collapsed.split(" ")
    out: list[str] = []
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            # An identifier after a unit marker: keep it exactly as typed.
            out.append(token)
            skip_next = False
            continue
        bare = token.strip(",.").lower()
        if bare in MINOR_WORDS and 0 < index < len(tokens) - 1:
            out.append(bare + ("," if token.endswith(",") else ""))
            continue
        if bare in UNIT_MARKERS:
            out.append("#" if bare == "#" else bare.capitalize())
            skip_next = True
            continue
        out.append(_word(token))

    text = " ".join(out)

    # Separate the street from the city, but only when the agent used no commas
    # at all. If they punctuated it themselves, their delimiting is the intent
    # and adding more would cut a city like "Havre de Grace" in half.
    if "," not in text:
        parts = text.split(" ")
        last_street_type = -1
        for index, token in enumerate(parts):
            if token.lower() in STREET_TYPES:
                last_street_type = index
        # Only if something follows it, and that something is not a unit.
        if 0 <= last_street_type < len(parts) - 1:
            following = parts[last_street_type + 1].strip(",.").lower()
            if following not in UNIT_MARKERS and not following.startswith("#"):
                parts[last_street_type] += ","
                text = " ".join(parts)

    # A state code should be preceded by a comma and followed by the postcode
    # with only a space. "baltimore md 21228" becomes "Baltimore, MD 21228".
    # A state token only counts when a postcode follows it, or when it ends the
    # address. Several state codes are also compass directions — NE is Nebraska
    # and also northeast — so "123 ne 4th st" would otherwise be punctuated as
    # though Nebraska appeared in the middle of a street name.
    parts = text.split(" ")
    for index, token in enumerate(parts):
        if token.strip(",") not in STATE_CODES or index == 0:
            continue
        is_last = index == len(parts) - 1
        followed_by_zip = not is_last and bool(_ZIP.match(parts[index + 1].strip(",")))
        if not (is_last or followed_by_zip):
            continue
        previous = parts[index - 1]
        if not previous.endswith(","):
            parts[index - 1] = previous + ","
        parts[index] = token.strip(",")
        break
    text = " ".join(parts)

    return re.sub(r"\s+,", ",", re.sub(r"(,\s*)+", ", ", text)).strip().strip(",").strip()
