"""Working out what each piece of text on a template actually means.

The 45 designs do not agree on how a fillable field is written. Across the eight
Just Listed slides alone there are three conventions:

    [PROPERTY ADDRESS]     a bracketed token
    PROPERTY ADDRESS       the bare words
    1234 Your Street       live sample data

A renderer that assumes one spelling fills nothing on the others and returns 200
doing it, which is the silent failure AGENTS.md §5 exists to prevent. So nothing
here assumes: `resolve()` reads the template's own text and reports which literal
means which field, and says what it could not identify.

Everything is pure. The text goes in, a mapping comes out, and the caller sends
the replacements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

#: Names typed into the designs as examples. They are not tokens and look like
#: real data, which is exactly why they must be recognised: an unreplaced sample
#: name reads as a correct flyer for the wrong agent.
SAMPLE_AGENT_NAMES: Final[tuple[str, ...]] = (
    "Kelsey Mahon",
    "Kelli Kulnich",
    "Louis Smith",
    "Kim Hixson",
    "Lolo Simmons",
    "Jason Vetter",
    "Stacey Abbott",
    "Tracey Edwards",
    "Piet de Dreu",
    "Melissa Hargreaves",
    # Client Review Post's footer, read from the live design 2026-08-14. Without
    # it the agent name never resolved, so this name would have printed on
    # somebody else's testimonial flyer.
    "Sebastion Johnson",
    "Sebastian Johnson",
)

#: Field name -> patterns that mean it, most specific first. Bracketed forms are
#: matched before bare words so "[PRICE]" is not consumed by the "price" rule.
#: Every sample contact detail found in Carmen's 69-page master design, read
#: from a PDF export on 2026-08-11 rather than guessed. These are **real Corner
#: House agents' real numbers and addresses**, left in the designs as sample
#: content, and any of them will print on another agent's flyer if the slot is
#: not filled. One already reached a delivered flyer.
SAMPLE_CONTACTS: Final[tuple[str, ...]] = (
    "443.326.7170",
    "410-564-6618",
    "410.952.6193",
    "443-799-6881",
    "443.605.5081",
    "443-986-0789",
    "410.456.6868",
    "808.225.8640",
    "410-999-9999",
    "kelli@cornerhouserealty.com",
    "louis@cornerhouserealty.com",
    "kirby-jay@cornerhouserealty.com",
    "name@cornerhouserealty.com",
    "sabbotthomes@gmail.com",
    "melissasellsmd@gmail.com",
)

#: Sample agent and client names in the master design, beyond the ones already
#: known. Same risk: a real person's name on somebody else's listing.
SAMPLE_PEOPLE: Final[tuple[str, ...]] = (
    "Regina Smith",
    "Louis Smith",
    "Jason Vetter",
    "Kelli Kulnich",
    "Melissa Hargreaves",
    "Realtor Name",
)

#: A weekday date as a design carries it for a sample open house, e.g.
#: "Sunday, Aug 2, 2026". Anchored whole-string so it cannot match body copy.
_SAMPLE_OPEN_HOUSE_DATE: Final[re.Pattern[str]] = re.compile(
    r"^(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?"
    r"(?:,?\s+\d{4})?$",
    re.IGNORECASE,
)

#: A bare time range, e.g. "2-4PM" or "11:30 AM - 1 PM". The en and em dashes
#: are deliberate: a designer typing a range in Slides gets one of them from
#: autocorrect as often as a plain hyphen.
_TIME_RANGE_ONLY: Final[re.Pattern[str]] = re.compile(
    r"^\d{1,2}(?::\d{2})?\s*(?:[AP]\.?M\.?)?\s*(?:-|–|—|to)\s*\d{1,2}(?::\d{2})?\s*[AP]\.?M\.?$",  # noqa: RUF001
    re.IGNORECASE,
)

#: The same time range, found anywhere inside a longer sentence, so a supplied
#: "Sunday, Aug 2, 2026 2-4PM" can be split between the two boxes a design uses.
_TIME_RANGE_INSIDE: Final[re.Pattern[str]] = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:[AP]\.?M\.?)?\s*(?:-|–|—|to)\s*\d{1,2}(?::\d{2})?\s*[AP]\.?M\.?",  # noqa: RUF001
    re.IGNORECASE,
)

#: A weekday date and a time range in one box, on separate lines, as
#: New Listing with Open House writes them.
_SAMPLE_OPEN_HOUSE_DATE_AND_TIME: Final[re.Pattern[str]] = re.compile(
    _SAMPLE_OPEN_HOUSE_DATE.pattern.rstrip("$") + r"\s+" + _TIME_RANGE_ONLY.pattern.lstrip("^"),
    re.IGNORECASE,
)

PATTERNS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "address": (
        re.compile(r"^\[\s*PROPERTY ADDRESS\s*\]$", re.IGNORECASE),
        re.compile(r"^PROPERTY ADDRESS$", re.IGNORECASE),
        re.compile(r"^Address$"),
        re.compile(r"^\d{1,6}\s+Your\s+Street.*$", re.IGNORECASE),
        re.compile(r"^\[\s*ADDRESS\s*\]$", re.IGNORECASE),
        # "123 ANYWHERE ST., ANY CITY" — a stock placeholder address that is
        # neither a token nor a real address, so neither existing rule caught it
        # and it printed on a finished flyer.
        re.compile(r"^\d+\s+ANYWHERE\s+ST\.?,?\s*ANY\s*CITY.*$", re.IGNORECASE),
        # A LIVE SAMPLE address, which is the third convention in the deck:
        # "5066 Winesap Way, Ellicott City, MD 21043" is not a token at all, it
        # is someone's real listing left in the design. Matched the same way
        # intake.address_looks_usable decides an address is real — a street
        # number plus a state or ZIP — so a headline like "JUST LISTED" cannot
        # be mistaken for one.
        re.compile(
            r"^\d{1,6}\s+\S.*\b(?:"
            + "|".join(
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
            + r")\b|^\d{1,6}\s+\S.*\b\d{5}\b",
            re.IGNORECASE,
        ),
    ),
    "price": (
        re.compile(r"^\[\s*PRICE\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*SALE PRICE\s*\]$", re.IGNORECASE),
        re.compile(r"^\$\s?[\d,]{3,12}$"),
    ),
    "beds": (
        re.compile(r"^\[\s*\d*\s*BEDS?\s*\]$", re.IGNORECASE),
        re.compile(r"^\d+\s*/?\s*Bedrooms?$", re.IGNORECASE),
        # Live sample data, as Open House carries it: "5 BEDS" with no bracket.
        # Square footage already accepted its unbracketed form below; without
        # the same for beds and baths, Open House stopped at needs_template
        # complaining about a fillable-looking field it could not name.
        re.compile(r"^\d+\s*/?\s*BEDS?$", re.IGNORECASE),
    ),
    "baths": (
        re.compile(r"^\[\s*\d*\s*BATHS?\s*\]$", re.IGNORECASE),
        re.compile(r"^\d+\s*/?\s*Bathrooms?$", re.IGNORECASE),
        re.compile(r"^\d+\s*/?\s*BATHS?$", re.IGNORECASE),
    ),
    "square_feet": (
        re.compile(r"^\[\s*SQ\.?\s*FT\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*SQFT\s*\]$", re.IGNORECASE),
        re.compile(r"^[\d,]+\s*/?\s*Sq\.?\s*FT$", re.IGNORECASE),
    ),
    "agent_name": (
        re.compile(r"^\[\s*AGENT NAME\s*\]$", re.IGNORECASE),
        re.compile(r"^AGENT NAME$", re.IGNORECASE),
        re.compile(r"^Realtor Name$", re.IGNORECASE),
        # Live sample names left in the designs. Without these a flyer for
        # Chase went out carrying "Kelsey Mahon" — the name was never a token,
        # so nothing replaced it, and the result looked entirely deliberate.
        re.compile(r"^(?:" + "|".join(SAMPLE_AGENT_NAMES) + r")$", re.IGNORECASE),
        re.compile(r"^(?:" + "|".join(re.escape(v) for v in SAMPLE_PEOPLE) + r")$", re.IGNORECASE),
    ),
    "client_name": (
        # The reviewer's name, not the agent's. These designs set it in caps
        # under the quote. Treating it as an agent slot puts the agent's name
        # where the client's belongs.
        re.compile(r"^OLIVIA WILSON$", re.IGNORECASE),
        re.compile(r"^\[\s*CLIENT\s*NAME\s*\]$", re.IGNORECASE),
    ),
    "review_quote": (
        # The sample testimonial, carried by every Client Review design with and
        # without its opening quotation mark.
        # Anchored at the start until 2026-08-14, when the live design was read:
        # its sample opens "Review goes here...Working with Corner House Realty",
        # so the anchor missed it and a stranger's testimonial would have
        # survived onto a real agent's flyer.
        re.compile(r".*Working with Corner House Realty was such a smooth.*", re.DOTALL),
        re.compile(r"^\"?Review goes here\.\.\..*", re.DOTALL),
        re.compile(r"^\[\s*(?:REVIEW|TESTIMONIAL|QUOTE)\s*\]$", re.IGNORECASE),
    ),
    "agent_title": (
        # Designs label the role as well as the name, and the sample text is
        # not a token: "REALTOR / TITLE" survived onto two finished flyers.
        re.compile(r"^\[?\s*REALTOR\s*/\s*TITLE\s*\]?$", re.IGNORECASE),
        re.compile(r"^\[\s*TITLE\s*\]$", re.IGNORECASE),
        # REALTOR is a membership credential, not harmless decorative copy.
        # The live Sold design carries this bare word beside the sample agent.
        # Treating it as brand text would let it survive for an agent whose
        # authoritative contact record supplies no title at all.
        re.compile(r"^REALTOR(?:®)?$", re.IGNORECASE),
    ),
    "social_handle": (
        # A stock handle from the design's own sample content. Pointing a real
        # flyer at somebody else's account is the same class of problem as
        # printing their phone number.
        re.compile(r"^@reallygreatsite$", re.IGNORECASE),
        re.compile(r"^\[\s*(?:SOCIAL|HANDLE|INSTAGRAM)\s*\]$", re.IGNORECASE),
    ),
    "neighborhood": (
        re.compile(r"^\[\s*NEIGHBORHOOD(?:\s*NAME)?\s*\]$", re.IGNORECASE),
        re.compile(r"^NEIGHBORHOOD NAME$", re.IGNORECASE),
        re.compile(r"^\[\s*NEIGHBORHOOD\s*NAME\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*CITY\s*/\s*AREA NAME\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*AREA NAME\s*\]\s*EDITION$", re.IGNORECASE),
    ),
    "agent_phone": (
        # Several designs hold both numbers in one text box, as
        # "C: 410.456.6868" and "O: 443.499.3839" on two lines. An anchored
        # single-number rule cannot match that, so the sample cell number
        # survived onto finished flyers even though it is in SAMPLE_CONTACTS.
        re.compile(
            r"^C:\s*(?:"
            + "|".join(re.escape(v) for v in SAMPLE_CONTACTS if "@" not in v)
            + r")\s+O:\s*[\d().\-\s]+$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:C:\s*)?(?:"
            + "|".join(re.escape(v) for v in SAMPLE_CONTACTS if "@" not in v)
            + r")$"
        ),
        re.compile(r"^\[\s*PHONE(?:\s*NUMBER)?\s*\]$", re.IGNORECASE),
        re.compile(r"^Phone$", re.IGNORECASE),
        re.compile(r"^\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}$"),
    ),
    "agent_email": (
        re.compile(
            r"^(?:" + "|".join(re.escape(v) for v in SAMPLE_CONTACTS if "@" in v) + r")$",
            re.IGNORECASE,
        ),
        re.compile(r"^\[?\s*EMAIL ADDRESS\s*\]?$", re.IGNORECASE),
        re.compile(r"^\[\s*EMAIL(?:\s*ADDRESS)?\s*\]$", re.IGNORECASE),
        re.compile(r"^Email(?:\s*address)?$", re.IGNORECASE),
        re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$"),
    ),
    "website": (
        re.compile(r"^\[\s*WEBSITE\s*\]$", re.IGNORECASE),
        re.compile(r"^Website$", re.IGNORECASE),
    ),
    "open_house": (
        re.compile(r"^\[\s*DAY\s*(?:&|AND|/)?\s*DATE\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*TIME\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*SATURDAY DATE\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*SUNDAY DATE\s*\]$", re.IGNORECASE),
        # Live sample data, as Open House carries it: a weekday date in one box
        # and a time range in another. Leaving these unrecognised was not
        # neutral — the design ships with a real previous listing's date and
        # time, so an unfilled box puts somebody else's open house on the flyer.
        _SAMPLE_OPEN_HOUSE_DATE,
        _TIME_RANGE_ONLY,
        # New Listing with Open House puts both in ONE box, on two lines:
        # "SUNDAY, MAY 24TH\n1 PM - 3 PM". Neither single-line pattern matches
        # that, so the tag kept a previous listing's open house.
        _SAMPLE_OPEN_HOUSE_DATE_AND_TIME,
    ),
}

#: Categories whose designs legitimately carry no property address.
ADDRESSLESS_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"Client Review", "Meet the Agent", "Neighborhood"}
)

#: Text that belongs to the design and must never be replaced. Matching one of
#: these is how "Just", "Listed" and the brand line survive a fill.
BRAND_TEXT: Final[frozenset[str]] = frozenset(
    {
        "just",
        "listed",
        "sold",
        "open house",
        "coming",
        "soon",
        "under",
        "contract",
        "corner house realty",
        "local experts. personal service.",
        "exceptional results.",
        "boutique service.",
        "local expertise.",
        "thinking of selling?",
        "let's connect.",
        "reach out today.",
        "sold for",
        "offers from",
        "offered at",
        "hosted by",
        # The heading above Client Review Post's quote. It labels the section
        # rather than naming a value, and Gable stopped at needs_template
        # calling it a fillable field it could not identify.
        "client testimonial",
        "real people",
        "real results",
        # The design uses a curly apostrophe; matching needs the same one.
        "let’s find your corner.",  # noqa: RUF001
        "let's find your corner.",
    }
)


@dataclass(frozen=True, slots=True)
class Resolution:
    """What a template's text was understood to mean."""

    #: Field name -> the literal text on the slide that carries it.
    fields: dict[str, str] = field(default_factory=dict)
    #: Text that looked fillable but matched no field. Worth reporting, because
    #: it is usually a convention nobody has taught this module yet.
    unrecognised: list[str] = field(default_factory=list)
    #: Fields the caller asked about that this template does not have.
    absent: list[str] = field(default_factory=list)
    #: Further literals carrying the same field. A design that labels the agent
    #: both "AGENT NAME" and "Realtor Name" has two, and filling only the first
    #: leaves the second on the finished flyer looking like a real caption.
    also: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def has_address(self) -> bool:
        """Whether an address slot was identified."""
        return "address" in self.fields

    def is_usable_for(self, category: str) -> bool:
        """Whether this template can be filled for a given kind of post.

        Address-less is not a fault for every design. A Client Review is a
        testimonial, a Meet the Agent is a profile, and a Neighborhood post is
        about an area — none of them carry a property address, and demanding one
        would reject 15 perfectly good templates.

        Args:
            category: The catalogue category, e.g. `Just Listed`.

        Returns:
            True when the template has the slots its category needs.

        Raises:
            Nothing.
        """
        if category in ADDRESSLESS_CATEGORIES:
            return bool(self.fields)
        return self.has_address


def _looks_fillable(text: str) -> bool:
    """Whether a piece of text is a candidate for replacement.

    Args:
        text: One shape's text.

    Returns:
        False for brand copy, long prose, and anything empty. A paragraph is
        never a field; a field is short.

    Raises:
        Nothing.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > 60:
        return False
    if stripped.lower() in BRAND_TEXT:
        return False
    return not stripped.lower().startswith(("local experts", "boutique", "a real estate"))


def resolve(texts: list[str], wanted: list[str] | None = None) -> Resolution:
    """Map a template's literals onto field names.

    Args:
        texts: Every text string on the slide, as read from the presentation.
        wanted: Fields the caller intends to fill. Any it asks for that the
            template does not have are reported in `absent` rather than
            silently skipped.

    Returns:
        A `Resolution`.

    Raises:
        Nothing.
    """
    found: dict[str, str] = {}
    also: dict[str, list[str]] = {}
    unrecognised: list[str] = []

    for raw in texts:
        text = " ".join(raw.split())
        matched = False
        for name, patterns in PATTERNS.items():
            if not any(pattern.match(text) for pattern in patterns):
                continue
            if name not in found:
                # Store the RAW text, not the normalised form. Patterns need
                # whitespace collapsed to match "4\nBedrooms" against a
                # single-line rule, but `replaceAllText` searches the slide
                # verbatim — so storing "4 Bedrooms" produced a literal that is
                # nowhere on the design, and the batch was refused for a
                # literal that "is not on the slide". That silently blocked 12
                # of the 45 templates, every one of them a design that wraps a
                # label onto two lines.
                found[name] = raw
            elif raw != found[name]:
                # A second literal for a slot already resolved. These designs
                # label the same thing more than one way, and replacing only
                # the first left "Realtor Name" and "123 ANYWHERE ST., ANY
                # CITY" sitting on finished flyers.
                also.setdefault(name, [])
                if raw not in also[name]:
                    also[name].append(raw)
            matched = True
            break
        # Recognised long prose, especially the sample testimonial, must be
        # tried before the generic short-field filter. The previous order made
        # the review rule unreachable for every realistic quote and allowed a
        # source testimonial to survive as if it were intentional body copy.
        if not matched and _looks_fillable(text) and ("[" in text or text.isupper()):
            unrecognised.append(text)

    absent = [name for name in (wanted or []) if name not in found]
    return Resolution(
        fields=found,
        unrecognised=unrecognised,
        absent=absent,
        also={name: tuple(extra) for name, extra in also.items()},
    )


#: Fields whose value is a fixed word the design typesets itself, rather than
#: something a person wrote. Only these follow the placeholder's capitalisation:
#: an address or a name must appear exactly as it was given, and upper-casing
#: those would rewrite what somebody typed.
_MATCH_PLACEHOLDER_CASE: Final[frozenset[str]] = frozenset({"agent_title"})

#: Fields whose box carries a number and the design's own word for what it
#: counts. The word belongs to the design, so a filled value keeps it.
_MEASUREMENT_FIELDS: Final[frozenset[str]] = frozenset({"beds", "baths", "square_feet"})

#: The digits and separators at the start of a measurement, e.g. `2,450` in
#: "2,450 SQFT" or in "2,450 square feet".
_LEADING_NUMBER: Final[re.Pattern[str]] = re.compile(r"^\s*([\d,]*\d)")


def _measurement_as_written(literal: str, value: str) -> str:
    r"""Write a measurement the way its own box already writes one.

    Open House sets its counts as "5 BEDS" and "6,348 SQFT"; New Listing sets
    them on two lines as "4\nBedrooms". Filling those with the words a person
    typed replaced the design's own label — an answered flyer read "4 beds" and
    "2,450" where the design reads "5 BEDS" and "6,348 SQFT", so the unit
    disappeared and the capitals with it.

    Args:
        literal: The design's own placeholder text, which supplies the unit.
        value: What Gable would otherwise write.

    Returns:
        The value's number carrying the literal's own trailing text. The value
        unchanged when it holds no number, and the number alone when the design
        writes none — a bracketed placeholder labels itself elsewhere.

    Raises:
        Nothing.
    """
    supplied = _LEADING_NUMBER.match(value)
    if not supplied:
        return value
    number = supplied.group(1)
    designed = _LEADING_NUMBER.match(literal)
    if not designed:
        return number
    return f"{number}{literal[designed.end() :]}"


def _as_written(name: str, literal: str, value: str) -> str:
    """Fill a fixed credential the way the design already writes it.

    New Listing sets REALTOR in capitals with a separately positioned
    superscript ® immediately after it. Filling "Realtor" left that mark
    stranded in open space, because the lower-case word is narrower than the
    placeholder it replaced. The design chose the capitals; Gable supplies the
    word and keeps its presentation.

    Args:
        name: The field being filled.
        literal: The design's own placeholder text.
        value: What Gable would otherwise write.

    Returns:
        The value, upper-cased when this is a design-typeset credential and the
        placeholder is upper-case. Everything else is returned untouched.

    Raises:
        Nothing.
    """
    if name == "open_house":
        return _open_house_part(literal, value)
    if name in _MEASUREMENT_FIELDS:
        return _measurement_as_written(literal, value)
    if name not in _MATCH_PLACEHOLDER_CASE:
        return value
    stripped = literal.strip()
    if stripped and stripped.isupper() and not value.isupper():
        return value.upper()
    return value


def _open_house_part(literal: str, value: str) -> str:
    """Give a design's date box the date and its time box the time.

    Open House sets the two in separate boxes. Filling both with the whole
    supplied string reads as a duplicate, and filling only one leaves the
    design's own sample — a real previous listing's date and time — on somebody
    else's flyer. So the supplied text is split when it plainly carries both.

    Args:
        literal: The design's own placeholder text.
        value: The open-house details exactly as they were supplied.

    Returns:
        The matching half, or the whole value when it cannot be split
        confidently. Never an empty string: a blank here would leave the
        design's sample showing.

    Raises:
        Nothing.
    """
    found = _TIME_RANGE_INSIDE.search(value)
    if not found:
        return value
    time_part = found.group(0).strip()
    remainder = (value[: found.start()] + " " + value[found.end() :]).strip(" ,-\u2013\u2014\t")
    date_part = " ".join(remainder.split()) or value
    # One box holding both, on two lines. Filling it with the whole string on a
    # single line overflowed the tag it sits in, so the shape is preserved.
    if _SAMPLE_OPEN_HOUSE_DATE_AND_TIME.match(literal.strip()):
        return f"{date_part}\n{time_part}"
    wants_time = bool(_TIME_RANGE_ONLY.match(literal.strip()) or "TIME" in literal.upper())
    return time_part if wants_time else date_part


def replacements(resolution: Resolution, values: dict[str, str]) -> dict[str, str]:
    """Turn resolved fields plus data into literal find/replace pairs.

    Only fields with a value are included. A field the data cannot fill is left
    alone so its placeholder stays visible — that is how a gap survives long
    enough for someone to be asked about it.

    Args:
        resolution: What the template's text means.
        values: Field name -> what it should say.

    Returns:
        `{literal_on_slide: new_text}`, ready for `replace_text`.

    Raises:
        Nothing.
    """
    pairs = {
        literal: _as_written(name, literal, values[name])
        for name, literal in resolution.fields.items()
        if values.get(name, "").strip()
    }
    # Every other literal carrying the same field, so a design that labels one
    # thing twice does not ship with the second label still showing.
    for name, extras in resolution.also.items():
        value = values.get(name, "").strip()
        if not value:
            continue
        for literal in extras:
            pairs[literal] = _as_written(name, literal, value)
    return pairs
