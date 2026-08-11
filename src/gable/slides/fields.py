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

#: Field name -> patterns that mean it, most specific first. Bracketed forms are
#: matched before bare words so "[PRICE]" is not consumed by the "price" rule.
PATTERNS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "address": (
        re.compile(r"^\[\s*PROPERTY ADDRESS\s*\]$", re.IGNORECASE),
        re.compile(r"^PROPERTY ADDRESS$", re.IGNORECASE),
        re.compile(r"^\d{1,6}\s+Your\s+Street.*$", re.IGNORECASE),
        re.compile(r"^\[\s*ADDRESS\s*\]$", re.IGNORECASE),
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
    ),
    "baths": (
        re.compile(r"^\[\s*\d*\s*BATHS?\s*\]$", re.IGNORECASE),
        re.compile(r"^\d+\s*/?\s*Bathrooms?$", re.IGNORECASE),
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
    ),
    "agent_phone": (
        re.compile(r"^\[\s*PHONE(?:\s*NUMBER)?\s*\]$", re.IGNORECASE),
        re.compile(r"^Phone$", re.IGNORECASE),
        re.compile(r"^\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}$"),
    ),
    "agent_email": (
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
        "realtor",
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
    unrecognised: list[str] = []

    for raw in texts:
        text = " ".join(raw.split())
        if not _looks_fillable(text):
            continue
        matched = False
        for name, patterns in PATTERNS.items():
            if name in found:
                continue  # first occurrence wins; templates repeat labels
            if any(pattern.match(text) for pattern in patterns):
                found[name] = text
                matched = True
                break
        if not matched and ("[" in text or text.isupper()):
            unrecognised.append(text)

    absent = [name for name in (wanted or []) if name not in found]
    return Resolution(fields=found, unrecognised=unrecognised, absent=absent)


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
    return {
        literal: values[name]
        for name, literal in resolution.fields.items()
        if values.get(name, "").strip()
    }
