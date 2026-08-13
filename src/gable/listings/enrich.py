"""Finding the source-displayed facts a flyer needs that nobody typed in.

Chase's rule: **a template must never ship with a displayed blank that is public
information.** After reading the selected source, an address is enough to
establish any beds, baths, square-footage, or price fields it displays, so Gable
looks those up rather than asking. A source with none of those fields does not
trigger this paid path. Only genuinely unknowable things — the hero photo, an
open-house time nobody published — are worth a question.

Two safeguards, because a confident wrong number on a client-facing flyer is
worse than a blank:

1. **Nothing is accepted without a source.** Every fact carries the URL it came
   from, and a fact with no source is discarded rather than used.
2. **The result must prove the property identity locally.** The exact street,
   city and ZIP must surround the values being extracted. A plausible number
   elsewhere on a search page is not evidence about this listing. Historical
   cached rows remain audit evidence but are not trusted by the current build
   path because they predate that identity proof.

Uses Firecrawl's search, which returns page content rather than just links, so
one call usually settles it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from sqlite3 import Connection
from typing import Any, Final

from gable import spend

#: Firecrawl search. https://docs.firecrawl.dev/api-reference/endpoint/search
_SEARCH_URL: Final[str] = "https://api.firecrawl.dev/v2/search"
_TIMEOUT_SECONDS: Final[int] = 90

#: Sites whose terms forbid scraping, or whose numbers are unreliable. Zillow is
#: excluded deliberately — CLAUDE.md §8 names scraping it as a terms violation.
BLOCKED_SOURCES: Final[tuple[str, ...]] = ("zillow.com", "trulia.com")

#: How the numbers actually appear on a listing page.
_BEDS: Final[re.Pattern[str]] = re.compile(
    r"(\d{1,2})\s*(?:bd\b|beds?\b|bedrooms?\b)", re.IGNORECASE
)
_BATHS: Final[re.Pattern[str]] = re.compile(
    r"(\d{1,2}(?:\.\d)?)\s*(?:ba\b|baths?\b|bathrooms?\b)", re.IGNORECASE
)
_SQFT: Final[re.Pattern[str]] = re.compile(
    r"([\d,]{3,7})\s*(?:sq\.?\s*ft|square\s*feet|sqft)", re.IGNORECASE
)
_PRICE: Final[re.Pattern[str]] = re.compile(
    r"(?:\b(?:list(?:ing)?|asking|sale)\s+price\b|\bprice\b|"
    r"\blisted\s+(?:for|at)\b)\s*(?:is|of|:|-)?\s*\$\s?([\d,]{5,12})"
    r"|\$\s?([\d,]{5,12})\s*(?:\b(?:list(?:ing)?|asking|sale)\s+price\b)",
    re.IGNORECASE,
)
_SEARCH_TERMS: Final[dict[str, str]] = {
    "beds": "bedrooms",
    "baths": "bathrooms",
    "square_feet": "square feet",
    "list_price": "price",
}


@dataclass(frozen=True, slots=True)
class Facts:
    """What was found about a property, and where each number came from."""

    beds: str = ""
    baths: str = ""
    square_feet: str = ""
    list_price: str = ""
    source_url: str = ""
    confidence: float = 0.0
    #: True only when the current lookup found the requested street, city and
    #: ZIP in the same result that supplied these values.  Historical cache rows
    #: predate this proof and are deliberately not promoted to trusted evidence.
    identity_verified: bool = False
    #: Anything that looked wrong, for Gable to mention rather than hide.
    caveats: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when nothing usable was found."""
        return not (self.beds or self.baths or self.square_feet or self.list_price)

    def as_dict(self) -> dict[str, str]:
        """The non-empty facts, for caching."""
        return {
            name: value
            for name, value in (
                ("beds", self.beds),
                ("baths", self.baths),
                ("square_feet", self.square_feet),
                ("list_price", self.list_price),
            )
            if value
        }


def _plausible(beds: str, baths: str, sqft: str) -> list[str]:
    """Sanity-check the numbers before they reach a flyer.

    A regex will happily read "24 beds" out of a page about an apartment block.
    These bounds are wide enough for any real house and narrow enough to catch
    a misparse.

    Args:
        beds: Bedrooms as found.
        baths: Bathrooms as found.
        sqft: Square footage as found.

    Returns:
        A caveat per implausible value, empty when all are sane.

    Raises:
        Nothing.
    """
    caveats: list[str] = []
    if beds and not (1 <= int(beds) <= 12):
        caveats.append(f"the bedroom count I found ({beds}) looks wrong")
    if baths and not (1 <= float(baths) <= 12):
        caveats.append(f"the bathroom count I found ({baths}) looks wrong")
    if sqft:
        digits = int(sqft.replace(",", ""))
        if not (200 <= digits <= 25000):
            caveats.append(f"the square footage I found ({sqft}) looks wrong")
    return caveats


def extract(text: str, source_url: str = "") -> Facts:
    """Pull property facts out of page text.

    Pure, so the parsing is testable without a network call.

    Args:
        text: Page content.
        source_url: Where it came from.

    Returns:
        Whatever could be read, with a confidence reflecting how much was found
        and whether it was plausible.

    Raises:
        Nothing.
    """
    beds_match = _BEDS.search(text)
    baths_match = _BATHS.search(text)
    sqft_match = _SQFT.search(text)
    price_match = _PRICE.search(text)

    beds = beds_match.group(1) if beds_match else ""
    baths = baths_match.group(1) if baths_match else ""
    sqft = sqft_match.group(1) if sqft_match else ""
    price = f"${next(group for group in price_match.groups() if group)}" if price_match else ""

    caveats = _plausible(beds, baths, sqft)
    found = sum(1 for value in (beds, baths, sqft, price) if value)
    confidence = min(0.95, 0.25 * found) if found else 0.0
    if caveats:
        confidence *= 0.5

    return Facts(
        beds=beds,
        baths=baths,
        square_feet=sqft,
        list_price=price,
        source_url=source_url,
        confidence=round(confidence, 2),
        caveats=caveats,
    )


def _source_url_is_usable(source_url: str) -> bool:
    """Return whether a fact source is one explicit web page."""
    parsed = urllib.parse.urlparse(source_url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def has_authoritative_source(facts: Facts) -> bool:
    """Return whether a current lookup proved both provenance and property identity."""
    return bool(
        not facts.is_empty and facts.identity_verified and _source_url_is_usable(facts.source_url)
    )


def _identity_text(value: str) -> str:
    """Fold punctuation while preserving address word order for exact matching."""
    decoded = urllib.parse.unquote(value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in decoded).split()
    )


def _phrase_pattern(value: str) -> re.Pattern[str] | None:
    """Compile a punctuation-tolerant phrase with exact token boundaries."""
    tokens = _identity_text(value).split()
    if not tokens:
        return None
    body = r"[\W_]+".join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


_OTHER_STREET_ADDRESS: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)\d{1,7}\s+[\w.'-]+(?:\s+[\w.'-]+){0,8}\s+"
    r"(?:st(?:reet)?|ave(?:nue)?|rd|road|dr(?:ive)?|ln|lane|way|"
    r"blvd|boulevard|ct|court|pl|place|ter(?:race)?|cir(?:cle)?|"
    r"pkwy|parkway|hwy|highway|trl|trail)\b",
    re.IGNORECASE,
)
_PROPERTY_WINDOW: Final[int] = 1_500


def _property_sections(address: str, result: Mapping[str, Any]) -> tuple[str, ...]:
    """Return bounded page sections that prove the exact requested property.

    Plausible bed, bath and price strings are not identity evidence.  The result
    section supplying them must repeat the requested street, city and ZIP.
    Starting at the exact street occurrence prevents a different listing above
    it from donating the first plausible number, while the next address and a
    hard window prevent an unrelated recommendation below it from doing so.
    """
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) < 3:
        return ()
    street = _phrase_pattern(parts[0])
    city = _phrase_pattern(parts[-2])
    zip_match = re.search(r"\b\d{5}(?:-\d{4})?\b", address)
    if street is None or city is None or zip_match is None:
        return ()
    unit: re.Pattern[str] | None = None
    for part in parts[1:-2]:
        folded = _identity_text(part)
        if re.match(r"^(?:unit|apt|apartment|suite|ste)\b", folded):
            unit = _phrase_pattern(part)
            break
    zip_pattern = re.compile(rf"(?<!\d){re.escape(zip_match.group(0))}(?!\d)")
    sections: list[str] = []
    for name in ("title", "description", "markdown"):
        raw = str(result.get(name) or "")
        for match in street.finditer(raw):
            end = min(len(raw), match.start() + _PROPERTY_WINDOW)
            next_address = _OTHER_STREET_ADDRESS.search(raw, match.end())
            if next_address is not None:
                end = min(end, next_address.start())
            section = raw[match.start() : end]
            if not city.search(section) or not zip_pattern.search(section):
                continue
            if unit is not None and not unit.search(section):
                continue
            sections.append(section)
    return tuple(sections)


def _search(query: str, api_key: str, limit: int = 4) -> list[Any]:
    """One Firecrawl search.

    Args:
        query: What to look for.
        api_key: The Firecrawl key.
        limit: How many results to fetch.

    Returns:
        Result dicts with `url` and `markdown`.

    Raises:
        urllib.error.URLError: on a transport failure.
    """
    body = json.dumps(
        {"query": query, "limit": limit, "scrapeOptions": {"formats": ["markdown"]}}
    ).encode()
    request = urllib.request.Request(
        _SEARCH_URL,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read())
    data = payload.get("data", {})
    results = data.get("web", data) if isinstance(data, dict) else data
    return results if isinstance(results, list) else []


def only_required(facts: Facts, required: frozenset[str]) -> Facts:
    """Discard facts and caveats the selected source does not display."""
    beds = facts.beds if "beds" in required else ""
    baths = facts.baths if "baths" in required else ""
    square_feet = facts.square_feet if "square_feet" in required else ""
    list_price = facts.list_price if "list_price" in required else ""
    caveats = _plausible(beds, baths, square_feet)
    count = sum(1 for value in (beds, baths, square_feet, list_price) if value)
    confidence = min(0.95, 0.25 * count) if count else 0.0
    if caveats:
        confidence *= 0.5
    return Facts(
        beds=beds,
        baths=baths,
        square_feet=square_feet,
        list_price=list_price,
        source_url=facts.source_url,
        confidence=round(confidence, 2),
        identity_verified=facts.identity_verified,
        caveats=caveats,
    )


def look_up(
    address: str,
    api_key: str,
    required: frozenset[str] = frozenset(_SEARCH_TERMS),
) -> Facts:
    """Research a property from its address.

    Args:
        address: A usable street address. Check `intake.address_looks_usable`
            first — a bad address wastes a paid call and returns confident
            nonsense.
        api_key: The Firecrawl key.
        required: Public fields present on the selected source. The search
            query and returned facts are both limited to these names.

    Returns:
        The best `Facts` found, or an empty one. Never raises: a failed lookup
        means Gable asks instead, which is a worse outcome than finding the
        numbers but a much better one than inventing them.

    Raises:
        Nothing.
    """
    if not required:
        return Facts()
    if not api_key:
        return Facts(caveats=["I have no way to search right now"])
    try:
        terms = " ".join(term for name, term in _SEARCH_TERMS.items() if name in required)
        results = _search(f"{address} {terms}", api_key)
    except Exception:
        return Facts(caveats=["I could not reach the search service"])

    best = Facts()
    for result in results:
        if not isinstance(result, Mapping):
            continue
        url = str(result.get("url", ""))
        if not _source_url_is_usable(url) or any(
            blocked in (urllib.parse.urlparse(url).hostname or "").lower()
            for blocked in BLOCKED_SOURCES
        ):
            # Their terms forbid it, and a flyer built on a violation is not
            # worth the numbers.
            continue
        for section in _property_sections(address, result):
            found = only_required(extract(section, url), required)
            found = Facts(
                beds=found.beds,
                baths=found.baths,
                square_feet=found.square_feet,
                list_price=found.list_price,
                source_url=found.source_url,
                confidence=found.confidence,
                identity_verified=True,
                caveats=found.caveats,
            )
            if found.confidence > best.confidence:
                best = found
    return best


def fill_gaps(known: dict[str, str], found: Facts) -> tuple[dict[str, str], list[str]]:
    """Merge researched facts into what the agent supplied, without overwriting.

    What the agent typed always wins. Gable's job is to fill blanks, not to
    correct a human's own listing — AGENTS.md §4 prohibition 7.

    Args:
        known: What came off the form, non-empty values only.
        found: What research turned up.

    Returns:
        `(merged, notes)`, where notes name each fact that was looked up rather
        than supplied, so the Slack message can say so.

    Raises:
        Nothing.
    """
    merged = dict(known)
    notes: list[str] = []
    for name, value in found.as_dict().items():
        if merged.get(name, "").strip():
            continue
        merged[name] = value
        notes.append(name.replace("_", " "))
    return merged, notes


def default_research(
    api_key: str,
    connection: Connection | None = None,
) -> Callable[[str, frozenset[str]], Facts]:
    """A research function bound to a Firecrawl key.

    Args:
        api_key: The Firecrawl key. Empty disables lookups, and the run then
            asks rather than researching.
        connection: Spend ledger for the live paid path. Tests and offline
            callers may omit it.

    Returns:
        A callable taking an address and the selected source's public fields.

    Raises:
        Nothing.
    """

    def research(
        address: str,
        required: frozenset[str] = frozenset(_SEARCH_TERMS),
    ) -> Facts:
        if connection is None or not api_key:
            return look_up(address, api_key, required)
        estimate = spend.Estimate(
            service="firecrawl",
            model="search",
            usd=spend.FIRECRAWL_PER_SEARCH,
            detail="one property search reservation",
        )
        try:
            return spend.guarded_call(
                connection,
                estimate,
                lambda: look_up(address, api_key, required),
            )
        except spend.BudgetExceededError:
            return Facts(caveats=["Testing has reached its spending limit"])

    return research
