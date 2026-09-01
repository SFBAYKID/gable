"""Tidying a property address without changing what it says.

Agents type addresses into a form at speed, and the form takes whatever they
type. Real submissions include `1225 canberwell rd baltimore md 21228`,
`4265 bright bay way Ellicott city 21042` and `10205 Douglas Ave, Silver
Spring, MD, 20902`. Each is understandable and none is presentable: this text
is set at 44pt across the middle of a flyer that goes to a client.

So the rules here are deliberately conservative. Capitalisation, punctuation and
spacing are fixed; **nothing is added or reordered**, and the only thing removed
is a trailing country, which no design prints and which fails every shape check
downstream. A misspelled street stays misspelled and a missing state stays
missing, because inventing either would put a different address on a flyer for a
real property — which is worse than an untidy one, and invisible to anyone
checking.

The one token this rewrites is a state the submission itself wrote out:
`Bowie Maryland 20716` becomes `Bowie, MD 20716`. That says what the agent said,
in the form every design prints and every check downstream reads. It is
restricted to the name sitting where the state belongs, because every state name
is also a street or a town.

Does not handle: validating that the address exists, expanding or abbreviating
street types, or inferring a state from a city. Those all require knowing
something the submission did not say.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
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

#: The same states written out, lower cased. An agent types what they say, so
#: `2519 Ann Arbor Lane Bowie Maryland 20716` arrived on 2026-08-21 and every
#: check downstream reads the state as a postal code — which left Gable telling
#: Carmen the address "has no state" while printing the state in the same
#: sentence. Folding the name to its code writes down what the submission
#: already said; it is never used to insert a state that is not written.
STATE_NAMES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "alabama": "AL",
        "alaska": "AK",
        "arizona": "AZ",
        "arkansas": "AR",
        "california": "CA",
        "colorado": "CO",
        "connecticut": "CT",
        "delaware": "DE",
        "district of columbia": "DC",
        "florida": "FL",
        "georgia": "GA",
        "hawaii": "HI",
        "idaho": "ID",
        "illinois": "IL",
        "indiana": "IN",
        "iowa": "IA",
        "kansas": "KS",
        "kentucky": "KY",
        "louisiana": "LA",
        "maine": "ME",
        "maryland": "MD",
        "massachusetts": "MA",
        "michigan": "MI",
        "minnesota": "MN",
        "mississippi": "MS",
        "missouri": "MO",
        "montana": "MT",
        "nebraska": "NE",
        "nevada": "NV",
        "new hampshire": "NH",
        "new jersey": "NJ",
        "new mexico": "NM",
        "new york": "NY",
        "north carolina": "NC",
        "north dakota": "ND",
        "ohio": "OH",
        "oklahoma": "OK",
        "oregon": "OR",
        "pennsylvania": "PA",
        "rhode island": "RI",
        "south carolina": "SC",
        "south dakota": "SD",
        "tennessee": "TN",
        "texas": "TX",
        "utah": "UT",
        "vermont": "VT",
        "virginia": "VA",
        "washington": "WA",
        "west virginia": "WV",
        "wisconsin": "WI",
        "wyoming": "WY",
    }
)

#: "District of Columbia" is the longest state name, at three words.
_MAX_STATE_NAME_WORDS: Final[int] = 3

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

#: "street, city, ST ZIP" — the one shape every design prints. One flyer
#: carried a ZIP and another did not, so the format is pinned and a missing
#: ZIP is a question rather than a render.
WHOLE_ADDRESS: Final[re.Pattern[str]] = re.compile(r"^.+,\s*.+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$")

#: Street types a comma may be placed after, for the repair in `tidy`. The
#: short list above finds the street's end when the agent used no commas; this
#: one, a superset, finds it when they used commas everywhere but there.
_BOUNDARY_TYPES: Final[frozenset[str]] = STREET_TYPES | frozenset(
    {"alley", "annex", "arcade", "bend", "expressway", "expy", "plaza", "terr", "xing", "crossing"}
)

# ASSUMPTION: an address already ending in a state and ZIP whose earlier text
# holds a recognised street type followed by words has its city in those final
# words. Every one of the 18 real rows this repairs reads that way; a street
# whose NAME ends in a street-type word followed by more street name would be
# cut early, and no submission has shown one.
_MISSING_CITY_COMMA: Final[re.Pattern[str]] = re.compile(
    r"^(?P<street>.+\b(?:"
    + "|".join(sorted(_BOUNDARY_TYPES, key=len, reverse=True))
    + r"))\s+(?P<city>[A-Z][A-Z .'-]+),\s*(?P<state>[A-Z]{2}\s+\d{5}(?:-\d{4})?)$",
    re.IGNORECASE,
)
_ZIP: Final[re.Pattern[str]] = re.compile(r"^\d{5}(-\d{4})?$")
_MC: Final[re.Pattern[str]] = re.compile(r"^(mc|mac)([a-z]{2,})$", re.IGNORECASE)

#: A country appended to an otherwise complete address. Address autocomplete
#: adds it, so a real submission arrived as "225 N Wycombe Ave Upper Darby, PA
#: 19082 United States". Every downstream check requires the text to end at the
#: ZIP, so the address failed validation and Gable asked Carmen to retype one
#: the form already held, correctly — the same failure the lower-case-state bug
#: caused, one regex away. Anchored at the end, so a street carrying these
#: letters mid-address is untouched.
_TRAILING_COUNTRY: Final[re.Pattern[str]] = re.compile(
    r"[,\s]+(?:united\s+states(?:\s+of\s+america)?|u\.?\s*s\.?\s*a\.?)\s*$",
    re.IGNORECASE,
)


def _word(token: str) -> str:
    """Capitalise one token according to what it is.

    Args:
        token: A single whitespace-delimited token, punctuation attached.

    Returns:
        The token cased for display. Numbers, ordinals and postcodes are left
        alone; directions are upper cased; `McCarthy` keeps its inner capital,
        which `str.title` destroys. A state code is NOT upper cased here: "Ct"
        is a court on most streets and Connecticut on almost none, and casing
        every such token as a state wrote "802 Dressage CT Bel Air" — a real
        submission. The state is found by position in `tidy` and cased there.

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
    if upper in DIRECTIONS:
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


def _state_index(parts: list[str]) -> int | None:
    """The first state position; see `_state_indices`."""
    found = _state_indices(parts)
    return found[0] if found else None


def _state_indices(parts: list[str]) -> list[int]:
    """Where the state sits in a space-split address, wherever a code is there.

    Args:
        parts: The address split on single spaces, commas still attached.

    Returns:
        Every index of a token that is a state code AND sits where a state
        belongs — last, or immediately before a ZIP. Position is the whole
        test: "Ct" mid-street is a court, "OR" between two lot numbers is a
        conjunction, "Maryland Ave" is a street, and none of them are the
        state. Never index zero, which is the house number. A field holding
        two listings has two, and both are cased, so the two-property question
        quotes it tidily rather than half-tidied.

    Raises:
        Nothing.
    """
    found: list[int] = []
    for index, token in enumerate(parts):
        if index == 0 or token.strip(",.").upper() not in STATE_CODES:
            continue
        is_last = index == len(parts) - 1
        followed_by_zip = not is_last and bool(_ZIP.match(parts[index + 1].strip(",.")))
        if is_last or followed_by_zip:
            found.append(index)
    return found


def _fold_state_name(text: str) -> str:
    """Write a spelled-out state as the postal code every design prints.

    Args:
        text: The address, already cased and split into space-delimited tokens.

    Returns:
        The same address with a trailing state name replaced by its two-letter
        code, or unchanged when no token is unambiguously the state. Only the
        name sitting immediately before the closing ZIP — or ending the address
        — is folded, because every state name is also a street or a town:
        "3701 Maryland Ave, Baltimore, MD 21218" must survive untouched.

    Raises:
        Nothing.
    """
    parts = text.split(" ")
    # A code already in the state's position means any name elsewhere in the
    # string is a place, not the state. "California, MD 20619" is a real
    # Maryland town. Position matters: "802 Dressage Ct Bel Air Maryland
    # 21014" has a "Ct" that is a court, and its state is still spelled out.
    if _state_index(parts) is not None:
        return text
    end = len(parts)
    if end and _ZIP.match(parts[-1].strip(",.")):
        end -= 1
    for length in range(_MAX_STATE_NAME_WORDS, 0, -1):
        start = end - length
        # Never the whole address: a bare "Maryland 20716" names no property.
        if start <= 0:
            continue
        phrase = " ".join(part.strip(",.") for part in parts[start:end]).lower()
        code = STATE_NAMES.get(phrase)
        if code is None:
            continue
        # The comma pass below wants the state bare, but a comma the agent typed
        # after it belongs to the string until then.
        trailing = "," if parts[end - 1].endswith(",") else ""
        return " ".join([*parts[:start], code + trailing, *parts[end:]])
    return text


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
        Nothing is added or reordered, and the only thing removed is a trailing
        country, which no design prints and which otherwise fails every shape
        check downstream. A street name spelled wrong on the form stays wrong on
        the flyer, because the alternative is guessing at a real property's
        address and being confidently mistaken.
    """
    if not address or not address.strip():
        return ""

    # Normalise separators first so tokens are clean, then rebuild the commas
    # we actually want. Submissions arrive with none, with doubles, and with
    # commas floating between spaces.
    collapsed = re.sub(r"\s*,\s*", ", ", " ".join(address.split()))
    collapsed = re.sub(r"(,\s*)+", ", ", collapsed).strip().strip(",").strip()
    # The one thing this function removes, and it is removed because it is not
    # part of the address any design prints: every template writes a US street,
    # city, state and ZIP. Left in place it ends the string after the ZIP, which
    # fails the shape check and turns a complete address into a question.
    collapsed = _TRAILING_COUNTRY.sub("", collapsed).strip().strip(",").strip()
    # "9411 Perry Hall Blvd, Baltimore MD 21236/" arrived with a slash on the
    # end, which left the ZIP not ending the string and the whole check false.
    collapsed = collapsed.rstrip(" ,./;:-")

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
        if 0 <= last_street_type < len(parts) - 1:
            boundary = last_street_type
            following = parts[boundary + 1].strip(",.").lower()
            # A unit is part of the street, not the start of the city, so the
            # split moves past it: "Ave Unit 118 Baltimore" divides after 118.
            # This used to refuse to divide at all, which left every condo
            # address without its city comma — "23 Pierside Ave Unit 118
            # Baltimore, MD 21230" then failed the flyer's address check and
            # stopped a run that had everything else it needed.
            if following in UNIT_MARKERS:
                # The marker and the identifier after it: "Unit 118", "# D".
                boundary += 2
            elif following.startswith("#"):
                # Marker and identifier in one token: "#D".
                boundary += 1
            # Still only if something follows, which is the city.
            if boundary < len(parts) - 1:
                parts[boundary] += ","
                text = " ".join(parts)

    # A state the agent wrote out becomes its code first, so the comma pass
    # below sees it and the flyer's shape check can read it.
    text = _fold_state_name(text)

    # A state code should be preceded by a comma and followed by the postcode
    # with only a space. "baltimore md 21228" becomes "Baltimore, MD 21228".
    # A state token only counts when a postcode follows it, or when it ends the
    # address. Several state codes are also compass directions — NE is Nebraska
    # and also northeast — so "123 ne 4th st" would otherwise be punctuated as
    # though Nebraska appeared in the middle of a street name.
    parts = text.split(" ")
    for state_at in _state_indices(parts):
        previous = parts[state_at - 1]
        if not previous.endswith(","):
            parts[state_at - 1] = previous + ","
        parts[state_at] = parts[state_at].strip(",.").upper()
    text = " ".join(parts)
    text = re.sub(r"\s+,", ",", re.sub(r"(,\s*)+", ", ", text)).strip().strip(",").strip()

    # The agent used commas, but not between the street and the city:
    # "1032 Foxwood Ln Essex, MD 21221". Eighteen of the 140 addresses the form
    # had received by 2026-09-01 were this shape, and each one fails the
    # whole-address check and asks for an address that is already complete.
    # Repaired only when the text already ends in a state and ZIP and a known
    # street type marks where the street stops; "Havre de Grace" survives
    # because no street type sits inside it. This lived in
    # `slides.manifest.normalise_address` for a while, which the runner used
    # and the thread announcement did not — two readers again.
    if not WHOLE_ADDRESS.match(text):
        repaired = _MISSING_CITY_COMMA.match(text)
        if repaired is not None:
            text = (
                f"{repaired.group('street')}, {repaired.group('city')}, {repaired.group('state')}"
            )
    return text


#: State codes that are also ordinary English words. Only these need a position
#: test; every other code is unambiguous wherever it appears.
_WORD_LIKE_STATE_CODES: Final[frozenset[str]] = frozenset(
    {"OR", "IN", "ME", "OK", "HI", "OH", "AS", "LA", "PA", "DE", "CT"}
)

#: A ZIP anywhere in the string. The shape check wants it at the end; naming
#: which of the two is wrong needs to know whether it is there at all.
ZIP_ANYWHERE: Final[re.Pattern[str]] = re.compile(r"\b\d{5}(?:-\d{4})?\b")

#: The house number that opens an address. Five-digit house numbers are
#: ordinary — "10600 Partridge Ln" — and to a ZIP pattern they are a ZIP. On
#: 2026-09-01 that read "10600 Partridge Ln Apt B3, Cockeysville, MD 21030" as
#: carrying two ZIPs, so Gable told Carmen the address "looks like more than
#: one property" and asked which one, three times, while she said it was one
#: condo. Earlier in the same thread it had read "10600 partridge lane b3" as
#: having a ZIP and reported only the state missing. A range ("10600-10602")
#: and a letter suffix ("1234A") are house numbers too.
_LEADING_HOUSE_NUMBER: Final[re.Pattern[str]] = re.compile(r"^\s*\d+(?:-\d+)?[A-Za-z]?\s+")


def zip_codes(address: str) -> list[str]:
    """Every ZIP-shaped token in an address, not counting its house number.

    Args:
        address: The address, as typed or tidied.

    Returns:
        The five-digit (or ZIP+4) tokens in order of appearance, with the
        leading house number left out. Anything else that is five digits long
        still counts, because a second house number mid-string belongs to a
        second property and the caller counting ZIPs wants to know about it.

    Raises:
        Nothing.
    """
    return ZIP_ANYWHERE.findall(_LEADING_HOUSE_NUMBER.sub("", address, count=1))


def incomplete_address(supplied: str) -> str:
    """Ask for the rest of an address that was supplied but cannot be printed.

    Not "I still need the address", and not "what is the full address?". Gable
    opens every listing thread by naming the property, so on 2026-08-19 it
    announced "4216 Norfolk Avenue, Baltimore 21216" and then told Carmen it
    still needed the address — which reads as a fault in Gable and sends her
    looking for something she had already sent.

    Args:
        supplied: The address as the request gives it, already tidied.

    Returns:
        One sentence naming what is missing and showing what is in hand.

    Raises:
        Nothing.

    Note:
        This lives here rather than in `pipeline.needs` because two separate
        checks reach it — the batched ask and `slides.manifest.validate` — and
        for a while only one of them used this wording. Frank Lancelotta III's
        listing on 2026-08-28 got the good sentence at 11:55 and the vague one
        at 13:55: "The address reads '3500 Hawks Hill Rd, Lot #1 & OR Lot # 2',
        and I could not separate the street, city, state and ZIP confidently.
        What is the full address?" Chase, reading it: "Why are you asking for
        the address, it's already in the thread." It was — Gable had printed it
        twice itself. Asking for the whole thing hides which part is missing.
    """
    address = " ".join(supplied.split())
    words = [word.strip(",").upper() for word in address.split()]
    # The value reaching here is already tidied, and `tidy` folds a state the
    # agent wrote out into its code, so a missing code is a genuinely missing
    # state rather than one this check could not read. That was not true on
    # 2026-08-21: "Bowie Maryland 20716" was told it had no state, twice.
    found = set(words) & STATE_CODES
    # A handful of state codes are also ordinary English words, and one of them
    # joined two lot numbers: "3500 Hawks Hill Rd, Lot #1 & OR Lot # 2" was read
    # as being in Oregon, so an address with no city, state or ZIP was reported
    # as merely missing its ZIP. Those codes count only where a state can sit —
    # at the end, as in "Portland, OR 97201". An unambiguous code still counts
    # anywhere, because a MISPLACED state is present and the sentence about
    # ordering is the honest one for it.
    tail = set(words[-3:])
    found -= {code for code in _WORD_LIKE_STATE_CODES if code not in tail}
    has_state = bool(found)
    # Not the house number: "10600 partridge lane b3" has none of the four
    # parts a design prints after the street, and was told only "no state".
    has_zip = bool(zip_codes(address))
    if not has_state and not has_zip:
        fault = "it has no state or ZIP code"
    elif not has_state:
        fault = "it has no state"
    elif not has_zip:
        # Named for what it is. "Not in the order the design prints" sent
        # somebody looking for a formatting fault in an address that was simply
        # missing its last five digits.
        fault = "it has no ZIP code"
    elif _state_index(address.split(" ")) is None:
        # The state is present but not where a state sits: "Baltimore MD 4216
        # Norfolk Avenue 21216". The ordering sentence is the honest one.
        fault = "it is not in the street, city, state and ZIP order the design prints"
    else:
        # "Not in the order the design prints" was said of "87 Twin Lakes
        # Gettysburg, PA 17325", which is in that order and merely lacks the
        # comma after a street with no street-type word. Name the real gap.
        fault = "I could not tell where the street ends and the city begins"
    return (
        f"I have this listing at {address}, but {fault}, so I cannot print it on the "
        "flyer. Send me the whole address and I will build it."
    )
