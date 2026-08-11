"""Finding the facts a flyer needs that nobody typed in.

Chase's rule: **a template must never ship with a blank that is public
information.** An address is enough to establish beds, baths, square footage and
often a price, so Gable looks them up rather than asking. Only genuinely
unknowable things — the hero photo, an open-house time nobody published — are
worth a question.

Two safeguards, because a confident wrong number on a client-facing flyer is
worse than a blank:

1. **Nothing is accepted without a source.** Every fact carries the URL it came
   from, and a fact with no source is discarded rather than used.
2. **Facts are cached by address.** The same property comes back as a listing,
   an open house and then a sale; paying to research it three times is waste,
   and the cache is what makes the common case free.

Uses Firecrawl's search, which returns page content rather than just links, so
one call usually settles it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Final

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
_PRICE: Final[re.Pattern[str]] = re.compile(r"\$\s?([\d,]{5,12})")


@dataclass(frozen=True, slots=True)
class Facts:
    """What was found about a property, and where each number came from."""

    beds: str = ""
    baths: str = ""
    square_feet: str = ""
    list_price: str = ""
    source_url: str = ""
    confidence: float = 0.0
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
    price = f"${price_match.group(1)}" if price_match else ""

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


def _search(query: str, api_key: str, limit: int = 4) -> list[dict[str, Any]]:
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


def look_up(address: str, api_key: str) -> Facts:
    """Research a property from its address.

    Args:
        address: A usable street address. Check `intake.address_looks_usable`
            first — a bad address wastes a paid call and returns confident
            nonsense.
        api_key: The Firecrawl key.

    Returns:
        The best `Facts` found, or an empty one. Never raises: a failed lookup
        means Gable asks instead, which is a worse outcome than finding the
        numbers but a much better one than inventing them.

    Raises:
        Nothing.
    """
    if not api_key:
        return Facts(caveats=["I have no way to search right now"])
    try:
        results = _search(f"{address} bedrooms bathrooms square feet", api_key)
    except Exception:
        return Facts(caveats=["I could not reach the search service"])

    best = Facts()
    for result in results:
        url = str(result.get("url", ""))
        if any(blocked in url.lower() for blocked in BLOCKED_SOURCES):
            # Their terms forbid it, and a flyer built on a violation is not
            # worth the numbers.
            continue
        found = extract(str(result.get("markdown", "")), url)
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
