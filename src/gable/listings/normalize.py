"""Raw sheet row to `Listing`: trimming, parsing, and validation.

Nothing here raises on bad data. Validation failures populate `Listing.problems`
and the orchestrator decides (ARCHITECTURE.md 4.2) — one malformed row must
never stop a batch.

**The column mapping is data, not code.** `ColumnMap` names which sheet header
feeds which field, so the live `Form Responses 1` headers can be corrected
without touching a line of logic. `DEFAULT_COLUMN_MAP` holds the headers
ARCHITECTURE.md 3.1 *expects*, and that document says plainly to confirm them
against the live sheet — so they are marked as assumptions and are designed to
be wrong. `describe_unmatched_headers` reports exactly which ones missed, which
means one run against the real sheet answers the question rather than a person
transcribing headers by hand.

Header matching is case-insensitive and whitespace-collapsed, so `"Agent Email "`
and `"agent email"` both hit the same field. Only cosmetic differences are
absorbed; a genuinely different header is reported, never guessed at.

Assumes: US phone numbers and USD prices. Both are marked `# ASSUMPTION:` at the
point of parsing.

Does not handle: network access. Every function here is pure and unit-tested.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Final

from gable.listings.address import tidy
from gable.models import Listing, derive_response_row_id, utc_now

_HORIZONTAL_WS = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_NON_DIGIT = re.compile(r"\D")
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ORDINAL = re.compile(r"^\d+(st|nd|rd|th)$", re.IGNORECASE)

#: Address tokens that must stay uppercase through title-casing. Without these,
#: "123 ne 4th st" becomes "123 Ne 4Th St" on a printed flyer.
_ALWAYS_UPPER: Final[frozenset[str]] = frozenset(
    {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NNE", "NNW", "SSE", "SSW", "PO"}
)

#: US phone numbers are 10 digits, optionally with a leading country code 1.
_US_NATIONAL_DIGITS: Final[int] = 10


@dataclass(frozen=True, slots=True)
class ColumnMap:
    """Which `Form Responses 1` header feeds which `Listing` field.

    Every value is a header string as it appears in the sheet. An empty string
    means "this column does not exist", which is a supported state — the form
    may not collect a photo at all (ARCHITECTURE.md 5).
    """

    timestamp: str = "Timestamp"
    agent_first_name: str = "Agent first name"
    agent_last_name: str = "Agent last name"
    agent_email: str = "Agent email"
    agent_phone: str = "Agent phone"
    address: str = "Property address"
    price: str = "Price"
    description: str = "Description"
    beds: str = "Beds"
    baths: str = "Baths"
    sqft: str = "Sq ft"
    photo: str = "Photo upload"

    def required_headers(self) -> tuple[str, ...]:
        """Headers without which a flyer cannot be built at all."""
        return tuple(h for h in (self.timestamp, self.agent_email, self.address) if h)

    def all_headers(self) -> tuple[str, ...]:
        """Every non-empty header this map references."""
        return tuple(
            value
            for value in (getattr(self, spec.name) for spec in fields(self))
            if isinstance(value, str) and value
        )


#: ASSUMPTION: these are the live headers. ARCHITECTURE.md 3.1 lists them as
#: *expected* and explicitly says to confirm against the real sheet, and the
#: photo column may not exist at all. Settled by running
#: `describe_unmatched_headers` against one real row — that is the intended way
#: to answer it, rather than transcribing headers by hand.
DEFAULT_COLUMN_MAP: Final[ColumnMap] = ColumnMap()


def fold_header(header: str) -> str:
    """Reduce a header to its match key: casefolded, whitespace-collapsed."""
    return _HORIZONTAL_WS.sub(" ", header.replace("\n", " ")).strip().casefold()


def describe_unmatched_headers(
    row_headers: Mapping[str, str] | tuple[str, ...], column_map: ColumnMap = DEFAULT_COLUMN_MAP
) -> tuple[str, ...]:
    """Report which mapped headers are absent from a real row.

    This is the diagnostic that turns "what are the real column names?" from a
    question into a command. Run it against one row of the live sheet and it
    names exactly what to correct in `ColumnMap`.

    Args:
        row_headers: A row mapping, or a tuple of header strings.
        column_map: The mapping to check.

    Returns:
        The mapped headers that were not found, in `ColumnMap` field order.
        Empty means the map is correct.
    """
    present = {
        fold_header(h)
        for h in (row_headers.keys() if isinstance(row_headers, Mapping) else row_headers)
    }
    return tuple(h for h in column_map.all_headers() if fold_header(h) not in present)


def clean_text(raw: str) -> str:
    """Trim and collapse whitespace without destroying paragraph structure.

    Runs of spaces and tabs collapse to one space; three or more newlines
    collapse to two. Single newlines survive, because a description written as
    paragraphs should still read as paragraphs in a text box.
    """
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    return _BLANK_LINES.sub("\n\n", _HORIZONTAL_WS.sub(" ", normalized)).strip()


def normalize_email(raw: str) -> tuple[str, str | None]:
    """Lowercase and validate an email's shape.

    Returns:
        `(email, problem)`. The email is returned lowercased even when the shape
        looks wrong — the submitted value is what a flyer shows, and Gable never
        "corrects" a contact detail (AGENTS.md 4.7).
    """
    email = clean_text(raw).lower()
    if not email:
        return "", "agent email is missing"
    if not _EMAIL_SHAPE.match(email):
        return email, f"agent email does not look like an address: {email!r}"
    return email, None


def format_phone_us(digits: str) -> str:
    """Render ten digits the way a flyer shows them: `(818) 259-7432`.

    Display format, not storage format. E.164 (`+18182597432`) is correct for
    dialling APIs and wrong for print — nobody puts a plus sign on a listing
    flyer. Gable's output is read by humans, so the human format wins.
    """
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def normalize_phone(raw: str) -> tuple[str, str | None]:
    """Normalize a US phone number to `(818) 259-7432`.

    Returns:
        `(phone, problem)`. On failure the cleaned original is returned rather
        than an empty string, so a flyer shows what the agent submitted instead
        of nothing, and the problem tells Carmen to check it.
    """
    cleaned = clean_text(raw)
    if not cleaned:
        return "", None  # Optional field; absence is not a problem.

    # ASSUMPTION: US numbering plan. Every agent on this roster is US-based.
    # Settled by the first non-US agent, at which point this needs a region.
    digits = _NON_DIGIT.sub("", cleaned)
    if len(digits) == _US_NATIONAL_DIGITS:
        return format_phone_us(digits), None
    if len(digits) == _US_NATIONAL_DIGITS + 1 and digits.startswith("1"):
        return format_phone_us(digits[1:]), None
    return cleaned, f"phone is not a 10-digit US number: {cleaned!r}"


def format_price(value: int) -> str:
    """Render a whole-dollar price the way a flyer shows it."""
    return f"${value:,}"


def parse_price(raw: str) -> tuple[int | None, str, str | None]:
    """Parse a price into a number and a display string.

    Deliberately does **not** invent a number it cannot read. "Price upon
    request" comes back with `value=None` and the original text preserved as the
    display value, plus a problem — putting a fabricated figure on a listing
    flyer is worse than showing what the agent wrote.

    Returns:
        `(value, display, problem)`. `display` is regenerated from `value` when
        parsing succeeded, so formatting is consistent across a batch.
    """
    cleaned = clean_text(raw)
    if not cleaned:
        return None, "", "price is missing"

    # ASSUMPTION: USD, whole dollars. Cents on a listing flyer are noise, and
    # every price in this market is a round figure.
    candidate = cleaned.replace("$", "").replace(",", "").replace(" ", "")
    try:
        value = round(float(candidate))
    except ValueError:
        return None, cleaned, f"price could not be parsed as a number: {cleaned!r}"
    if value < 0:
        return None, cleaned, f"price is negative: {cleaned!r}"
    return value, format_price(value), None


def title_case_address(raw: str) -> str:
    """Present an address properly. See `listings.address.tidy` for the rules.

    Args:
        raw: The address exactly as the agent typed it.

    Returns:
        The same address, cased and punctuated for a flyer. Nothing is added,
        removed, corrected or reordered.

    Raises:
        Nothing.

    Note:
        This used to leave any mixed-case input alone, on the theory that mixed
        case carried authorial intent. It does not: `2808 Berwick Ave,
        Baltimore, Md 21234` is mixed case, so `Md` survived onto a rendered
        flyer. Punctuation went unhandled entirely, which is how `1225
        canberwell rd baltimore md 21228` reached a 44pt headline.
    """
    return tidy(clean_text(raw))


def truncate_on_word_boundary(text: str, limit: int) -> tuple[str, bool]:
    """Truncate to `limit` characters without splitting a word.

    The ellipsis counts toward the limit: the limit exists because of the
    template's text box, so the returned string never exceeds it.

    Args:
        text: The text to truncate.
        limit: Maximum length of the result, including the ellipsis.

    Returns:
        `(text, was_truncated)`. Carmen is told when truncation happened so she
        knows to check the layout (ARCHITECTURE.md 4.3).
    """
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False

    window = text[: limit - 1]
    boundary = window.rfind(" ")
    # A single word longer than the limit has no boundary to cut on; a hard cut
    # is the only option and is still better than overflowing the box.
    kept = window[:boundary] if boundary > 0 else window
    return kept.rstrip(" ,.;:—-") + "…", True


def build_agent_name(first: str, last: str) -> str:
    """Join first and last name, tolerating either being absent."""
    return " ".join(part for part in (clean_text(first), clean_text(last)) if part)


def row_to_listing(
    row: Mapping[str, str],
    *,
    column_map: ColumnMap = DEFAULT_COLUMN_MAP,
    max_description_chars: int = 400,
) -> Listing:
    """Convert one raw form row into a validated `Listing`.

    Never raises. Every parse failure lands in `Listing.problems`.

    Args:
        row: The raw row, keyed by sheet header.
        column_map: Which header feeds which field.
        max_description_chars: Truncation limit from configuration.

    Returns:
        A `Listing`. Check `problems` and `is_flyer_ready` before using it.
    """
    lookup = {fold_header(key): value for key, value in row.items()}

    def cell(header: str) -> str:
        return lookup.get(fold_header(header), "") if header else ""

    problems: list[str] = []

    raw_timestamp = clean_text(cell(column_map.timestamp))
    if not raw_timestamp:
        problems.append("submission timestamp is missing")

    email, email_problem = normalize_email(cell(column_map.agent_email))
    if email_problem:
        problems.append(email_problem)

    address = title_case_address(cell(column_map.address))
    if not address:
        problems.append("property address is missing")

    phone, phone_problem = normalize_phone(cell(column_map.agent_phone))
    if phone_problem:
        problems.append(phone_problem)

    price_value, price_display, price_problem = parse_price(cell(column_map.price))
    if price_problem:
        problems.append(price_problem)

    agent_name = build_agent_name(
        cell(column_map.agent_first_name), cell(column_map.agent_last_name)
    )
    if not agent_name:
        problems.append("agent name is missing")

    description, truncated = truncate_on_word_boundary(
        clean_text(cell(column_map.description)), max_description_chars
    )
    if truncated:
        problems.append(f"description truncated to {max_description_chars} characters")

    return Listing(
        # Hashed from the raw timestamp cell, not a parsed datetime: parsing
        # needs the form's timezone, which is unknown until the live tab is read.
        response_row_id=derive_response_row_id(raw_timestamp, email, address),
        submitted_at=_parse_timestamp(raw_timestamp),
        agent_email=email,
        agent_name=agent_name,
        address=address,
        price_display=price_display,
        price_value=price_value,
        agent_phone=phone,
        description=description,
        description_truncated=truncated,
        beds=clean_text(cell(column_map.beds)),
        baths=clean_text(cell(column_map.baths)),
        sqft=clean_text(cell(column_map.sqft)),
        form_photo_url=clean_text(cell(column_map.photo)),
        problems=tuple(problems),
    )


def _parse_timestamp(raw: str) -> datetime:
    """Best-effort parse of the Timestamp cell, falling back to now().

    ASSUMPTION: Google Forms writes `YYYY-MM-DD HH:MM:SS` or ISO 8601, without a
    timezone. Which one, and in which zone, is unknown until the live sheet is
    read — so this tries both and falls back rather than failing a listing over
    a display field. `response_row_id` deliberately hashes the raw string, so
    identity does not depend on this parse being right.
    """
    for pattern in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).astimezone()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).astimezone()
    except ValueError:
        return utc_now()
