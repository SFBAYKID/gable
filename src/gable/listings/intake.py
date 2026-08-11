"""The eleven columns that matter, and what each one means.

Chase named them: **B, C, E, L, N, O, P, Q, R, S, T**. Everything else on the
form is deliberately ignored — the postcard branch and the video branch are
unused (0 of 99 rows), and column K, the social-media content type, is out of
scope because Gable makes flyers rather than reels.

Column E is the hinge. Its value is the request type, and it maps onto a
template category in `slides/catalog.py`, so "Open House" on the form finds the
Open House designs in Drive. That is the whole routing decision.

The second idea here is **completeness**. A template must never ship with a
publicly knowable fact left blank. Square footage, beds and baths are matters of
public record for a given address, so `missing_public_facts()` names what has to
be looked up rather than asked about. Only genuinely unknowable things — the
hero photo, an open-house time nobody has published — are worth a question.

The third is **coherence**. A sold listing with no closing price is not missing
data, it is contradictory data, and `incoherences()` says so in the words Gable
will use to ask.

Everything here is pure: a row in, a decision out. No network, no Sheets client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: Spreadsheet letters to zero-based indices, so this module can be read against
#: the sheet itself. Only the eleven Chase named.
COLUMNS: Final[dict[str, int]] = {
    "B": 1,  # Email Address — the join key to Sales_People
    "C": 2,  # Name of Agent
    "E": 4,  # Select your request type — picks the template category
    "L": 11,  # Property Address
    "N": 13,  # Include details for post
    "O": 14,  # Open house date/time (if applicable)
    "P": 15,  # New price (if price improvement)
    "Q": 16,  # Closing price (for sold posts only)
    "R": 17,  # Additional Notes for Social Media Team
    "S": 18,  # Buyer or seller side
    "T": 19,  # Notes
}

#: Form wording on the left, catalogue category on the right. The form's own
#: values are inconsistent — one row says "just listed" in lower case — so the
#: match is case-folded and the raw value is always kept for the audit trail.
REQUEST_TYPE_TO_CATEGORY: Final[dict[str, str]] = {
    "new listing": "Just Listed",
    "just listed": "Just Listed",
    "new listing with open house": "Just Listed",
    "open house": "Open House",
    "sold": "Just Sold",
    "under contract": "Under Contract",
    "price reduction": "Just Listed",
    "client review post": "Client Review",
    "coming soon": "Coming Soon",
    "end of year brag post": "",  # no design exists for this yet
}

#: Facts that are a matter of public record for any address, and so must be
#: looked up rather than asked about.
#: Keys match `enrich.Facts.as_dict()` exactly. They disagreed once — this said
#: "price" while Facts said "list_price" — so a fact that had already been
#: looked up was never recognised and got researched again every time.
PUBLIC_FACTS: Final[tuple[str, ...]] = ("beds", "baths", "square_feet", "list_price")


@dataclass(frozen=True, slots=True)
class Intake:
    """One form submission, reduced to the fields Gable acts on."""

    agent_email: str
    agent_name: str
    request_type: str
    address: str
    post_details: str
    open_house: str
    new_price: str
    closing_price: str
    extra_notes: str
    side: str
    notes: str

    @property
    def category(self) -> str:
        """The template category this request routes to.

        Returns:
            A category name matching `slides/catalog.py`, or an empty string if
            the request type has no design. Empty is a question for Carmen, not
            a reason to pick something close.

        Raises:
            Nothing.
        """
        return REQUEST_TYPE_TO_CATEGORY.get(self.request_type.strip().lower(), "")

    @property
    def price(self) -> str:
        """Whichever price column this request type actually populates.

        The form has no list price. A sold post carries a closing price and a
        price reduction carries a new price, so the request type decides which
        column is the price for this flyer.

        Returns:
            The price as the agent typed it, or an empty string.

        Raises:
            Nothing.
        """
        return (self.closing_price or self.new_price).strip()

    @property
    def is_sold(self) -> bool:
        """Whether this request is about a completed sale."""
        return self.request_type.strip().lower() in {"sold", "under contract"}

    @property
    def mentions_open_house(self) -> bool:
        """Whether the request implies an open house, from any column."""
        lowered = self.request_type.strip().lower()
        return "open house" in lowered or bool(self.open_house.strip())


def from_row(row: list[str]) -> Intake:
    """Read one sheet row into the fields Gable uses.

    Args:
        row: A raw row from `Form Responses 1`. Short rows are normal — Sheets
            truncates trailing empties — so every access is bounds-checked.

    Returns:
        An `Intake` with every value stripped.

    Raises:
        Nothing. A malformed row must never stop a batch, so this cannot raise;
        `missing_public_facts` and `incoherences` report what is wrong instead.
    """

    def cell(letter: str) -> str:
        index = COLUMNS[letter]
        return row[index].strip() if len(row) > index else ""

    return Intake(
        agent_email=cell("B").lower(),
        agent_name=cell("C"),
        request_type=cell("E"),
        address=cell("L"),
        post_details=cell("N"),
        open_house=cell("O"),
        new_price=cell("P"),
        closing_price=cell("Q"),
        extra_notes=cell("R"),
        side=cell("S"),
        notes=cell("T"),
    )


@dataclass(frozen=True, slots=True)
class Question:
    """Something Gable must ask, and why it cannot answer it itself."""

    field_name: str
    #: The sentence Gable says. Written to be posted verbatim.
    ask: str
    #: True when no amount of searching could settle it.
    only_a_human_knows: bool = False


def missing_public_facts(intake: Intake, known: dict[str, str] | None = None) -> list[str]:
    """Facts the flyer needs that are public record, so must be researched.

    Args:
        intake: The submission.
        known: Anything already resolved, e.g. from a previous lookup.

    Returns:
        Field names to go and find. Never asked of Carmen — an address is enough
        to establish beds, baths and square footage, and making her type them is
        the work this product exists to remove.

    Raises:
        Nothing.
    """
    have = {k for k, v in (known or {}).items() if v.strip()}
    if intake.price:
        have.add("list_price")
    if not intake.address:
        # Without an address nothing can be looked up; that is a separate problem
        # and `incoherences` raises it.
        return []
    return [fact for fact in PUBLIC_FACTS if fact not in have]


#: Words that suggest an address is not really an address. The form's address
#: column has been used for a Google review link and for two addresses at once.
_NOT_AN_ADDRESS: Final[re.Pattern[str]] = re.compile(r"https?://|google review", re.IGNORECASE)

#: US state abbreviations. Corner House works the Mid-Atlantic, but listing the
#: lot costs nothing and avoids a surprise the first time someone sells in a
#: state nobody thought to include.
_STATES: Final[tuple[str, ...]] = (
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
)


def address_looks_usable(address: str) -> bool:
    """Whether an address is worth sending to a lookup service.

    A street number and a few words are not enough: the live sheet contains
    "SRES Listing 29 Maple" in the address column, which passes that test and is
    not an address. Requiring a state or a ZIP separates the two, because every
    real US address carries one and the accidental values do not.

    Args:
        address: The value from column L.

    Returns:
        False when it is a URL, has no street number, or names no state or ZIP.
        A bad lookup wastes a paid call and comes back with confident nonsense
        about a property that does not exist.

    Raises:
        Nothing.
    """
    stripped = address.strip()
    if len(stripped) < 8 or _NOT_AN_ADDRESS.search(stripped):
        return False
    if not re.search(r"\d", stripped):
        return False
    has_zip = bool(re.search(r"\b\d{5}(?:-\d{4})?\b", stripped))
    has_state = bool(re.search(r"\b(?:" + "|".join(_STATES) + r")\b", stripped, re.IGNORECASE))
    return has_zip or has_state


def incoherences(intake: Intake) -> list[Question]:
    """Contradictions worth asking about, in the words Gable will use.

    These are not missing fields. They are combinations that cannot both be
    true, and guessing past one puts a wrong number on a client-facing flyer.

    Args:
        intake: The submission.

    Returns:
        Questions, most consequential first. Empty when the row hangs together.

    Raises:
        Nothing.
    """
    asks: list[Question] = []

    if not intake.address:
        asks.append(
            Question(
                "address",
                "This request came through without a property address, so I cannot "
                "look anything up or build the flyer. What is the address?",
                only_a_human_knows=True,
            )
        )

    elif not address_looks_usable(intake.address):
        asks.append(
            Question(
                "address",
                f"I cannot make sense of the address on this one — it reads "
                f"{intake.address.strip()!r}. What is the property address?",
                only_a_human_knows=True,
            )
        )

    if intake.is_sold and not intake.closing_price:
        asks.append(
            Question(
                "closing price",
                f"This one is marked {intake.request_type.lower()} but there is no closing "
                "price on it. Do you have that?",
                only_a_human_knows=True,
            )
        )

    if intake.request_type.strip().lower() == "price reduction" and not intake.new_price:
        asks.append(
            Question(
                "new price",
                "This is a price reduction, but no new price came through. What is it now?",
                only_a_human_knows=True,
            )
        )

    if intake.mentions_open_house and not intake.open_house.strip():
        asks.append(
            Question(
                "open house date and time",
                "This is an open house post and I do not have the date or time for it. When is it?",
                only_a_human_knows=True,
            )
        )

    if intake.request_type and not intake.category:
        asks.append(
            Question(
                "template",
                f"I do not have a design for a {intake.request_type.lower()} post. "
                "Which template should I use, or should I skip this one?",
                only_a_human_knows=True,
            )
        )

    if intake.closing_price and intake.new_price:
        asks.append(
            Question(
                "price",
                "This request has both a new price and a closing price on it. "
                "Which one belongs on the flyer?",
                only_a_human_knows=True,
            )
        )

    return asks


#: How a second agent actually arrives. Row 84 of the live sheet reads
#: "Listed by: Stacey Abbott. Hosted by: Jason Vetter" in column N, and slide 28
#: is a two-agent Open House design carrying exactly those two names. So the
#: dual-agent case is not missing from the form after all — it is prose in the
#: details column, and this is where it gets read.
_ROLE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("listing", re.compile(r"listed\s*by\s*:?\s*([^.;,\n]+)", re.IGNORECASE)),
    ("hosting", re.compile(r"hosted\s*by\s*:?\s*([^.;,\n]+)", re.IGNORECASE)),
    ("co-listing", re.compile(r"co-?listed\s*by\s*:?\s*([^.;,\n]+)", re.IGNORECASE)),
)


def named_agents(intake: Intake) -> dict[str, str]:
    """Agents named in the post details, by the role the text gives them.

    Args:
        intake: The submission.

    Returns:
        A mapping such as `{"listing": "Stacey Abbott", "hosting": "Jason
        Vetter"}`. Empty when the details name nobody, which is the common case.

    Raises:
        Nothing.

    Note:
        The submitter is not assumed to be either. On row 84 the submitter is
        Jason Vetter and he is the *hosting* agent, while Stacey Abbott holds the
        listing — putting the submitter in the listing slot would be wrong, and
        wrong in a way that looks perfectly correct on the flyer.
    """
    text = context_text(intake)
    found: dict[str, str] = {}
    for role, pattern in _ROLE_PATTERNS:
        match = pattern.search(text)
        if match:
            name = " ".join(match.group(1).split())
            if name:
                found[role] = name
    return found


def context_text(intake: Intake) -> str:
    """Every form field that can explain how the requested post should work.

    The template decision cannot read only the request-type dropdown. Agents
    explain dual representation, hosting roles, dates, and desired calls to
    action in the three free-text sections, while the structured open-house and
    side fields add context of their own.

    Args:
        intake: The parsed form row.

    Returns:
        Normalized prose containing all selection-relevant fields.

    Raises:
        Nothing.
    """
    parts = (
        intake.request_type,
        intake.post_details,
        intake.open_house,
        intake.extra_notes,
        intake.side,
        intake.notes,
    )
    return ". ".join(" ".join(part.split()) for part in parts if part.strip())


_MULTIPLE_AGENT_SIGNAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:two|both)\s+agents?\b|\bco-?agents?\b|\bco-?list(?:ed|ing)?\b",
    re.IGNORECASE,
)


def mentions_multiple_agents(intake: Intake) -> bool:
    """Whether the notes imply a multi-agent post, even without two parsed names."""
    return bool(_MULTIPLE_AGENT_SIGNAL.search(context_text(intake)))


def needs_two_agents(intake: Intake) -> bool:
    """Whether this request names more than one person.

    Args:
        intake: The submission.

    Returns:
        True when two distinct names appear across the roles, which is the
        signal to reach for a dual-agent design rather than a single one.

    Raises:
        Nothing.
    """
    return len({name.lower() for name in named_agents(intake).values()}) > 1
