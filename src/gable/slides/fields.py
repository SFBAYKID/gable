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

from gable.listings.intake import ADDRESSLESS_CATEGORIES
from gable.slides.placeholders import (  # noqa: F401 - re-exported for callers
    _SAMPLE_OPEN_HOUSE_DATE,
    _SAMPLE_OPEN_HOUSE_DATE_AND_TIME,
    _STRANDED_SEPARATOR,
    _TIME_RANGE_INSIDE,
    _TIME_RANGE_ONLY,
    _TRAILING_JOINER,
    BRAND_TEXT,
    PATTERNS,
    SAMPLE_AGENT_NAMES,
    SAMPLE_CONTACTS,
    SAMPLE_PEOPLE,
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

#: Fields whose placeholder is a plausible value rather than a visible gap. A
#: bare "3" in a bathrooms slot cannot be told from a real bathroom count, and
#: three delivered flyers carried one: Andy Jang's bedrooms, Lina Mariner's
#: asking price, Mike Nugent's bathrooms. Blanked, so the flyer reads as
#: incomplete rather than as wrong. See DECISIONS.md, 2026-08-19.
#:
#: `open_house` belongs here by that same rule and was missed. The Open House
#: design ships "Sunday, Aug 2, 2026" and "2-4PM" -- a previous listing's real
#: open house, at 5066 Winesap Way. An unanswered date therefore did not read
#: as a gap; it read as this house being open on a specific afternoon. It is
#: the worst member of this set, because beds and baths are checked by anyone
#: who visits and a date and time is acted on by driving somewhere.
BLANK_WHEN_UNFILLED: Final[frozenset[str]] = frozenset(
    {"beds", "baths", "square_feet", "price", "open_house"}
)

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


#: A time range written longhand: "4pm to 6pm", "10:00 AM - 12:00 PM".
_SPELLED_TIME_RANGE: Final[re.Pattern[str]] = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*(?:-|\u2013|\u2014|to)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?$",
    re.IGNORECASE,
)


def compact_time(text: str) -> str:
    """Rewrite a time range in the compact form these designs are drawn for.

    Effie Fafaleos' open house was supplied as "4pm to 6pm". The Open House
    design's time box is 72pt wide and holds its own placeholder "2-4PM" at
    24pt, so the longhand form wrapped to three lines and overflowed downward
    into the stats row, printing "6pm" across "3 BATHS". Gable said the fit was
    too small to read and delivered it anyway, which is the right call for a
    flyer and the wrong one for a value it could simply have written the way
    the design writes it.

    A shared meridiem is stated once at the end, which is how every one of
    these designs sets it.

    Args:
        text: One time range, already isolated from its date.

    Returns:
        The compact form, or the text unchanged when it is not a plain range.

    Raises:
        Nothing.
    """
    found = _SPELLED_TIME_RANGE.match(text.strip())
    if found is None:
        return text
    start_hour, start_min, start_mer, end_hour, end_min, end_mer = found.groups()
    start = f"{start_hour}:{start_min}" if start_min else start_hour
    end = f"{end_hour}:{end_min}" if end_min else end_hour
    left = (start_mer or "").replace(".", "").upper()
    right = (end_mer or "").replace(".", "").upper()
    if left and right and left != right:
        return f"{start}{left}-{end}{right}"
    # One meridiem, said once: "4pm to 6pm" is "4-6PM", never "4PM-6PM".
    return f"{start}-{end}{right or left}"


def _wants_time(literal: str) -> bool:
    """Whether this box is the design's time box rather than its date box."""
    return bool(_TIME_RANGE_ONLY.match(literal.strip()) or "TIME" in literal.upper())


#: An hour range carrying no am or pm, at the very end of the value. On its own
#: this is ambiguous — "Aug 8-9" is two days, not two o'clock — so `_bare_hours`
#: only accepts it when what remains still reads as a date.
_BARE_HOUR_RANGE_AT_END: Final[re.Pattern[str]] = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:-|–|—|to)\s*\d{1,2}(?::\d{2})?\s*$",  # noqa: RUF001
    re.IGNORECASE,
)

#: What has to survive in the date half for a bare range to have been a time.
_A_DAY_NUMBER: Final[re.Pattern[str]] = re.compile(r"\d")

#: The same meridiem-less range, found anywhere. Only ever consulted with the
#: day-number context check `_bare_hours` uses, because on its own this matches
#: the "8-9" of "Aug 8-9" — a range of days, not of hours.
_BARE_HOUR_RANGE_INSIDE: Final[re.Pattern[str]] = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:-|–|—|to)\s*\d{1,2}(?::\d{2})?",  # noqa: RUF001
    re.IGNORECASE,
)


def _bare_times_before(value: str, end: int) -> list[re.Match[str]]:
    """Every meridiem-less time before `end`, judged by `_bare_hours`' own rule.

    A candidate counts as a time only when the text before it still carries a
    day number once separators are stripped — the same context test that keeps
    "Aug 8-9" a pair of days. In "Aug 8, 11-1 and Aug 9, 11-1" the inner "11-1"
    qualifies (before it stands "Aug 8,"), while in "Aug 8-9, 11-1" the "8-9"
    does not (before it stands only "Aug").

    Args:
        value: The open-house details exactly as supplied.
        end: Look only before this index — the trailing match's start.

    Returns:
        The qualifying matches, in order, spans intact so the caller can cut
        exactly these and nothing else.

    Raises:
        Nothing.
    """
    head = value[:end]
    return [
        match
        for match in _BARE_HOUR_RANGE_INSIDE.finditer(head)
        if _A_DAY_NUMBER.search(head[: match.start()].strip(" ,-–—\t"))  # noqa: RUF001
    ]


def _bare_hours(value: str) -> re.Match[str] | None:
    """Find a trailing hour range written without am or pm.

    "Saturday August 8, 11-1" is how people write an open house, and the strict
    pattern requires a meridiem, so the whole string went into the date box and
    the time box was emptied. The flyer then read "Saturday August 8, 11-1 | |
    $325,000", with the design's own separators standing either side of a gap.

    The reason the strict pattern is strict is that a bare range is genuinely
    ambiguous: "Aug 8-9" is two days. So this splits only when the date half
    still carries a day number afterwards. "Saturday August 8, 11-1" leaves
    "Saturday August 8," and splits; "Aug 8-9" would leave "Aug" and does not.

    Args:
        value: The open-house details exactly as supplied.

    Returns:
        The match for the time half, or None when nothing can be split safely.

    Raises:
        Nothing.
    """
    found = _BARE_HOUR_RANGE_AT_END.search(value)
    if found is None:
        return None
    remainder = value[: found.start()].strip(" ,-–—\t")  # noqa: RUF001
    return found if _A_DAY_NUMBER.search(remainder) else None


def open_house_occasions(value: str) -> int:
    """How many different times this open-house text names.

    Shares `_open_house_part`'s rule exactly, so the two can never disagree
    about whether a value is splittable: the same times are gathered, and two
    written the same way count once. "08/08 11am-1pm, 08/09 11am-1pm" is one
    open house held twice and fits a single time box; three different hours do
    not, and no width of box makes them.

    Args:
        value: The open-house details exactly as they were supplied.

    Returns:
        The number of distinct times named. Zero when no time was supplied.

    Raises:
        Nothing.
    """
    times = [match.group(0) for match in _TIME_RANGE_INSIDE.finditer(value)]
    if not times:
        found = _bare_hours(value)
        if found is None:
            return 0
        times = [
            *(match.group(0) for match in _bare_times_before(value, found.start())),
            found.group(0),
        ]
    return len({"".join(item.split()).casefold() for item in times})


#: A conjunction left at the front once the first occasion is cut away.
_LEADING_JOINER: Final[re.Pattern[str]] = re.compile(r"^(?:and|&|then|plus)\s+", re.IGNORECASE)


def open_house_left_off(resolution: Resolution, value: str) -> str:
    """What this design cannot show of an open-house request, in its own words.

    The answer depends on the design, not only on the value. A single box
    holding date and time together takes the whole request; only a design with
    a separate time box has to choose, because one time box holds one time.

    Args:
        resolution: The design's recognised placeholders.
        value: The open-house details exactly as they were supplied.

    Returns:
        The occasions that will not reach the flyer, or "" when none are lost.

    Raises:
        Nothing.
    """
    literals = [resolution.fields.get("open_house", ""), *resolution.also.get("open_house", ())]
    if any(_SAMPLE_OPEN_HOUSE_DATE_AND_TIME.match(item.strip()) for item in literals if item):
        return ""
    if open_house_occasions(value) < 2:
        return ""
    return dropped_open_houses(value)


def _earliest_time(value: str) -> re.Match[str] | None:
    """The first span in this text that counts as a time, by the splitter's rule.

    `_bare_hours` deliberately finds the TRAILING range, because that is the one
    a single open house ends with. Reused unchanged here it made "Aug 8, 11-1
    and Aug 9, 2-4" report "2-4" as the first time, so the whole string looked
    like one occasion and neither box was filled.

    Args:
        value: The open-house details exactly as they were supplied.

    Returns:
        The match, or None when the text names no time.

    Raises:
        Nothing.
    """
    inside = _TIME_RANGE_INSIDE.search(value)
    if inside is not None:
        return inside
    trailing = _bare_hours(value)
    if trailing is None:
        return None
    earlier = _bare_times_before(value, trailing.start())
    return earlier[0] if earlier else trailing


def first_open_house(value: str) -> str:
    """Trim open-house details to the first date and time they name.

    Args:
        value: The open-house details exactly as they were supplied.

    Returns:
        The text up to and including the first time named, or the whole value
        when it names no time at all.

    Raises:
        Nothing.
    """
    match = _earliest_time(value)
    if match is None:
        return value
    return value[: match.end()].strip(" ,-\u2013\u2014\t")


def dropped_open_houses(value: str) -> str:
    """The occasions that will not reach the flyer, for Gable to name.

    Args:
        value: The open-house details exactly as they were supplied.

    Returns:
        The remainder after the first occasion, or "" when there is none.

    Raises:
        Nothing.
    """
    match = _earliest_time(value)
    if match is None:
        return ""
    rest = value[match.end() :].strip(" ,-\u2013\u2014\t")
    # "and Aug 9, 2-4" reads as a fragment when Gable names it in a sentence.
    return _LEADING_JOINER.sub("", rest).strip(" ,-\u2013\u2014\t")


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
        confidently. The date half may come back empty when only a time was
        supplied — an explicit empty replacement clears the box, and
        clearing the sample date beats printing the time twice.

    Raises:
        Nothing.
    """
    with_meridiem = _TIME_RANGE_INSIDE.search(value)
    found = with_meridiem or _bare_hours(value)
    if not found:
        # No time was supplied at all. The date box takes the whole value; the
        # time box is emptied rather than repeating it. Sydney Kinney's open
        # house came through as "7/11/2026" and the flyer printed that date
        # twice, once in each box. Leaving the design's own "2-4PM" showing
        # would be worse still — that is a previous listing's real time.
        return "" if _wants_time(literal) else value
    time_part = compact_time(found.group(0).strip())
    earlier: list[re.Match[str]] = []
    if with_meridiem is not None:
        times = [match.group(0) for match in _TIME_RANGE_INSIDE.finditer(value)]
    else:
        # A bare range counts as a time only with a day number before it, and
        # the same rule finds the earlier copies. Without this, "Aug 8, 11-1
        # and Aug 9, 11-1" measured its times with the meridiem pattern alone,
        # found none, and left the first "11-1" standing in the date box above
        # its own time box — the exact failure the meridiem forms had
        # already been cured of.
        earlier = _bare_times_before(value, found.start())
        times = [*(match.group(0) for match in earlier), time_part]
    # Two days at the same hours \u2014 "08/08/2026 11am-1pm , 08/09/2026 11am-1pm" \u2014
    # is one time written twice. Taking out only the first left the second in
    # the date box, so the flyer read "08/08/2026 , 08/09/2026 11am-1pm" above
    # its own "11am-1pm". Two DIFFERENT times are two facts: dropping one would
    # be a lie about when the house is open, so that case is left as it was.
    same_time = len({"".join(item.split()).casefold() for item in times}) == 1
    # Two DIFFERENT times do not split, however they are written. Promoting one
    # to the time box asserts it as THE open-house time while the others hide in
    # the date line above it. That rule was applied to bare forms only, so
    # Effie Fafaleos' three open houses on 2026-08-20 -- "Friday, Aug. 21 4pm to
    # 6pm, Sat. Aug. 22 10am to 12pm, Sun, Aug. 23 11am to 1pm" -- were split
    # into "4pm to 6pm" beneath a date box reading "Friday, Aug. 21, Sat. Aug.
    # 22 10am to 12pm, Sun, Aug. 23 11am to 1pm". Only the width check stopped
    # that reaching a flyer; a wider box would have shipped it.
    #
    # `open_house_occasions` reports the same count, so the build can name what
    # it left off. Chase's rule of 2026-08-17 decides what happens next: a flyer
    # that exists beats a withheld flyer plus a description of one. The first
    # occasion goes on it, the rest are named in the delivery message, and
    # Carmen rebuilds for another date with one sentence if she wants it.
    if not same_time:
        if _SAMPLE_OPEN_HOUSE_DATE_AND_TIME.match(literal.strip()):
            # One box holding date and time together. The whole request goes in
            # it and nothing has to be dropped; if it does not fit, the width
            # check says so. Only a design with a SEPARATE time box forces a
            # choice, because one time box holds one time.
            return value
        first = first_open_house(value)
        if first == value:
            # No first occasion could be isolated, so nothing may claim a box.
            return "" if _wants_time(literal) else value
        return _open_house_part(literal, first)
    if with_meridiem is not None:
        remainder = _TIME_RANGE_INSIDE.sub(" ", value)
    else:
        # Cut exactly the spans judged to be times, newest last. A blanket
        # sub of the bare pattern would also take the "8-9" out of
        # "August 8-9, 11-1", which is a pair of days.
        # Every bare span judged a time, in order. `earlier` is empty for the
        # meridiem path, which never reaches here.
        spans = [*((m.start(), m.end()) for m in earlier), (found.start(), found.end())]
        pieces, last = [], 0
        for start, stop in spans:
            pieces.append(value[last:start])
            last = stop
        pieces.append(value[last:])
        remainder = " ".join(pieces)
    remainder = remainder.strip(" ,-\u2013\u2014\t")
    remainder = _STRANDED_SEPARATOR.sub(", ", remainder)
    # The word that joined the date to the time it no longer sits beside.
    # "08/01 and 08/02 from 12-2pm" left "08/01 and 08/02 from" in the date box,
    # with the "from" dangling at the end of the line.
    remainder = _TRAILING_JOINER.sub("", remainder).strip(" ,-\u2013\u2014\t")
    # No `or value` fallback: a value that was nothing but a time — "2-4PM"
    # — used to fall back to itself here and printed the time in both
    # boxes, the Sydney Kinney duplicate mirrored. Empty clears the sample.
    date_part = " ".join(remainder.split())
    # One box holding both, on two lines. Filling it with the whole string on a
    # single line overflowed the tag it sits in, so the shape is preserved.
    if _SAMPLE_OPEN_HOUSE_DATE_AND_TIME.match(literal.strip()):
        return f"{date_part}\n{time_part}" if date_part else time_part
    return time_part if _wants_time(literal) else date_part


def fields_sharing_a_literal(resolution: Resolution) -> dict[str, list[str]]:
    """Find placeholders that two different fields would both try to fill.

    `replacements` is keyed by the literal, so two fields resolving to one
    placeholder silently collapse: the second overwrites the first, and
    `replace_text` then swaps every occurrence on the slide. A design drawn
    with three bedrooms and three bathrooms would render a four-bed two-bath
    listing as two and two, with nothing to show it had happened. The
    standalone-literal guard cannot see it, because both occurrences really are
    standalone.

    Args:
        resolution: What the template's text means.

    Returns:
        `{literal: [field names]}` for every literal claimed by more than one
        field. Empty when the design is unambiguous, which all six are today.

    Raises:
        Nothing.
    """
    claimed: dict[str, list[str]] = {}
    for name, literal in resolution.fields.items():
        claimed.setdefault(literal, []).append(name)
    return {literal: names for literal, names in claimed.items() if len(names) > 1}


def replacements(resolution: Resolution, values: dict[str, str]) -> dict[str, str]:
    """Turn resolved fields plus data into literal find/replace pairs.

    Only fields with a value are included, so a field the data cannot fill is
    left alone and its placeholder stays visible — that is how a gap survives
    long enough for someone to be asked about it.

    The exception is a placeholder nobody can read as a gap. A stat slot is
    drawn with a real-looking number, so leaving it showing states a fact about
    somebody's house that nobody supplied. Those are blanked; see
    `BLANK_WHEN_UNFILLED`.

    Args:
        resolution: What the template's text means.
        values: Field name -> what it should say.

    Returns:
        `{literal_on_slide: new_text}`, ready for `replace_text`.

    Raises:
        Nothing.
    """
    pairs: dict[str, str] = {}
    for name, literal in resolution.fields.items():
        value = values.get(name, "").strip()
        if value:
            pairs[literal] = _as_written(name, literal, value)
        elif name in BLANK_WHEN_UNFILLED:
            pairs[literal] = ""
    # Every other literal carrying the same field, so a design that labels one
    # thing twice does not ship with the second label still showing.
    for name, extras in resolution.also.items():
        value = values.get(name, "").strip()
        if not value and name not in BLANK_WHEN_UNFILLED:
            continue
        for literal in extras:
            pairs[literal] = _as_written(name, literal, value) if value else ""
    return pairs
