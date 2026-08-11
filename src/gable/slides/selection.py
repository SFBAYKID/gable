"""Choose a template by what the submission is asking the design to do.

The request-type dropdown chooses a broad catalogue category. It does not say
whether the post represents one agent or two, carries one open-house date or a
Saturday-and-Sunday schedule, emphasizes stats, or asks for a private-tour call
to action. Those signals live across the notes fields. This module reads all of
them and applies explicit purpose metadata to the 45 catalogue entries.

Selection is deterministic and conservative: functional constraints are hard
filters; wording cues rank the remaining layouts; a documented default handles
a plain request; a genuine tie returns no choice so the runner asks instead of
guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from gable.listings.intake import Intake, context_text, needs_two_agents
from gable.slides.catalog import TemplateEntry, for_category


@dataclass(frozen=True, slots=True)
class TemplatePurpose:
    """The functional context one catalogue design supports."""

    agent_count: int
    includes_open_house: bool
    supports_two_dates: bool
    cues: tuple[str, ...]
    is_default: bool
    use_when: str


@dataclass(frozen=True, slots=True)
class SubmissionSignals:
    """Selection-relevant meaning extracted from the entire form row."""

    text: str
    agent_count: int
    includes_open_house: bool
    has_two_dates: bool


_DEFAULT_SLIDE: Final[dict[str, int]] = {
    "Client Review": 3,
    "Just Listed": 15,
    "Just Sold": 20,
    "Just Rented": 22,
    "Open House": 26,
    "Meet the Agent": 49,
    "Neighborhood": 57,
    "Coming Soon": 69,
    "Under Contract": 74,
}

# Words that make a layout's own copy appropriate. These are cues, not
# keyword-only routing: agent count, open-house use, and date count are hard
# constraints applied separately.
_CUES: Final[dict[int, tuple[str, ...]]] = {
    3: ("testimonial", "review", "quote"),
    4: ("real people", "real results", "results"),
    5: ("agent card", "contact the agent"),
    6: ("unique", "personalized", "personalised"),
    7: ("call", "dm", "direct message"),
    10: ("offers", "offer"),
    11: ("schedule", "private tour"),
    12: ("book a tour", "dm for details", "details"),
    13: ("stats", "beds", "baths", "square feet", "sqft"),
    14: ("boutique", "service"),
    15: ("clean", "simple", "standard"),
    16: ("hosted by", "hosting agent"),
    17: ("offered at", "list price"),
    19: ("thinking of selling", "sell your home"),
    20: ("sold for", "agent card"),
    21: ("let's connect", "lets connect", "connect"),
    22: ("rent", "rented", "per month", "lease"),
    23: ("local expert", "address"),
    24: ("beds", "baths", "square feet", "sqft", "stats"),
    26: ("full details", "tour with us"),
    27: ("dm me", "direct message"),
    28: ("two dates", "both days", "saturday and sunday"),
    29: ("private tour", "book a tour"),
    30: ("join us", "this weekend"),
    31: ("saturday and sunday", "sat and sun", "both days", "two dates"),
    49: ("local expert", "value", "why work with"),
    50: ("market area", "years of experience", "experience"),
    51: ("fun fact", "clients love"),
    52: ("local roots", "let's connect", "lets connect"),
    54: ("specialty", "next home"),
    57: ("schools", "commute", "weekend", "coffee", "eats"),
    58: ("vibe", "at a glance", "top pick"),
    59: ("local favorites", "local favourites", "community"),
    60: ("explore", "three favorites", "three favourites"),
    61: ("local guide", "dining", "parks"),
    64: ("exclusive preview", "get on the list"),
    65: ("garage", "beds", "baths", "sqft", "square feet"),
    66: ("fall in love",),
    67: ("vip", "exclusive preview"),
    68: ("first to know", "notify"),
    69: ("coming soon", "on the way"),
    71: ("thinking of selling", "sell your home"),
    72: ("sold for", "price"),
    73: ("multiple offers", "offers"),
    74: ("agent card", "contact"),
}

_OPEN_HOUSE_ADDONS: Final[frozenset[int]] = frozenset({16, 17})
_TWO_DATE_LAYOUTS: Final[frozenset[int]] = frozenset({28, 31})
_DAY = re.compile(r"\b(?:sat(?:urday)?|sun(?:day)?)\b", re.IGNORECASE)
_NUMERIC_DATE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")


def signals_for(intake: Intake) -> SubmissionSignals:
    """Read agent count, open-house use, and date shape from the full row."""
    text = context_text(intake).lower()
    days = {_normal_day(day) for day in _DAY.findall(text)}
    dates = set(_NUMERIC_DATE.findall(text))
    has_two_dates = (
        len(days) >= 2
        or len(dates) >= 2
        or any(phrase in text for phrase in ("two dates", "both days", "sat and sun"))
    )
    return SubmissionSignals(
        text=text,
        agent_count=2 if needs_two_agents(intake) else 1,
        includes_open_house=intake.mentions_open_house,
        has_two_dates=has_two_dates,
    )


def _normal_day(day: str) -> str:
    """Collapse abbreviated and full day names."""
    return "saturday" if day.lower().startswith("sat") else "sunday"


def purpose_for(entry: TemplateEntry) -> TemplatePurpose:
    """Return explicit usage metadata for every catalogue entry."""
    open_house = entry.category == "Open House" or entry.slide in _OPEN_HOUSE_ADDONS
    two_dates = entry.slide in _TWO_DATE_LAYOUTS
    agent_count = 2 if entry.dual_agent else 1
    details: list[str] = [f"{agent_count}-agent {entry.category.lower()} post"]
    if open_house:
        details.append("with open-house details")
    if two_dates:
        details.append("for two distinct dates")
    details.append(f"using the {entry.label.lower()} layout")
    return TemplatePurpose(
        agent_count=agent_count,
        includes_open_house=open_house,
        supports_two_dates=two_dates,
        cues=_CUES.get(entry.slide, ()),
        is_default=_DEFAULT_SLIDE.get(entry.category) == entry.slide,
        use_when="; ".join(details),
    )


def rank(category: str, intake: Intake) -> tuple[TemplateEntry, ...]:
    """Return eligible entries best-first, or empty on a genuine top tie."""
    signals = signals_for(intake)
    scored: list[tuple[int, TemplateEntry]] = []
    for entry in for_category(category):
        purpose = purpose_for(entry)
        if purpose.agent_count != signals.agent_count:
            continue
        if category == "Just Listed" and purpose.includes_open_house != signals.includes_open_house:
            continue
        if category == "Open House" and purpose.supports_two_dates != signals.has_two_dates:
            continue
        cue_score = sum(10 for cue in purpose.cues if cue in signals.text)
        default_score = 1 if purpose.is_default else 0
        scored.append((cue_score + default_score, entry))
    if not scored:
        return ()
    scored.sort(key=lambda item: (-item[0], item[1].slide))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return ()
    return tuple(entry for _score, entry in scored)
