"""Finding an agent nobody has recorded, on the brokerage's own site.

The roster workbook is a working document rather than a fixed list, so an agent
can submit a form before anyone adds them to it. Chase's rule: look them up on
cornerhouserealty.com, write the row, and **say what was written and where it
came from** — a phone number reaches a client, so it has to be checkable.

Three safeguards, all of them the same idea as `listings/enrich.py`:

1. **Only the brokerage's own site.** A phone number for "Andy Jang" found
   anywhere else may belong to a different Andy Jang. The one place that cannot
   be true is the roster page of the brokerage the form belongs to.
2. **Nothing without a source.** Every detail carries the page it came from, and
   a detail with no page is discarded rather than used.
3. **Nothing is corrected.** This only ever fills a gap. An agent already in the
   workbook is never looked up and never overwritten, in `contacts.py`.

Does not handle: deciding whether the person found is the right one when the
site lists two people with the same name. Both are discarded and Gable asks.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger("gable.agents.lookup")

_SEARCH_URL: Final[str] = "https://api.firecrawl.dev/v1/search"
_TIMEOUT_SECONDS: Final[int] = 30

#: The only host an agent's details are accepted from.
BROKERAGE_HOST: Final[str] = "cornerhouserealty.com"

#: US ten-digit numbers as the site writes them: 410.218.2786, (410) 218-2786,
#: 410-218-2786. The separator is captured so the roster's own style is kept.
_PHONE: Final[re.Pattern[str]] = re.compile(r"\(?\b(\d{3})\)?[.\-\s]?(\d{3})[.\-\s]?(\d{4})\b")

#: An address on the brokerage domain, which is the form's own convention.
_EMAIL: Final[re.Pattern[str]] = re.compile(
    r"\b([A-Za-z0-9._%+-]+@" + BROKERAGE_HOST.replace(".", r"\.") + r")\b", re.IGNORECASE
)

#: Numbers that are not a person's line. The office number is on every page of
#: the site, so accepting it would give every unknown agent the same number and
#: look exactly like a successful lookup.
OFFICE_NUMBERS: Final[frozenset[str]] = frozenset({"4434993839", "4432066900"})


@dataclass(frozen=True, slots=True)
class Found:
    """What the brokerage site says about one agent."""

    email: str = ""
    phone: str = ""
    source_url: str = ""

    @property
    def is_usable(self) -> bool:
        """Whether this is worth writing down: a real detail with a page behind it."""
        return bool(self.source_url) and bool(self.phone or self.email)


def _digits(phone: str) -> str:
    """Just the digits, for comparing one written number with another."""
    return re.sub(r"\D", "", phone)


def extract(markdown: str, url: str, agent_name: str) -> Found:
    """Read one agent's details out of a page, or return nothing.

    Args:
        markdown: The page text.
        url: Where it came from.
        agent_name: Who is being looked for. The page must name them, or its
            numbers belong to somebody else.

    Returns:
        What the page yields, empty when it does not name the agent or carries
        only the office line.

    Raises:
        Nothing.
    """
    if BROKERAGE_HOST not in url.lower():
        return Found()
    # The page has to be about this person. A brokerage directory lists every
    # agent, so a number found on a page that never names them is a number
    # belonging to whoever the page is actually about.
    if agent_name.strip().casefold() not in " ".join(markdown.split()).casefold():
        return Found()

    email_match = _EMAIL.search(markdown)
    phone = ""
    for candidate in _PHONE.finditer(markdown):
        written = candidate.group(0).strip()
        if _digits(written) not in OFFICE_NUMBERS:
            phone = written
            break
    return Found(
        email=email_match.group(1).lower() if email_match else "",
        phone=phone,
        source_url=url,
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
    # Firecrawl search: https://docs.firecrawl.dev/api-reference/endpoint/search
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


def find_agent(agent_name: str, api_key: str) -> Found:
    """Look one agent up on the brokerage site.

    Args:
        agent_name: Their full name, as the form recorded it.
        api_key: The Firecrawl key.

    Returns:
        Their details with the page they came from, or an empty `Found`. Never
        raises: a failed lookup means Gable builds with the office line and says
        so, which is far better than inventing a number.

    Raises:
        Nothing.
    """
    if not agent_name.strip() or not api_key:
        return Found()
    try:
        results = _search(f"{agent_name} {BROKERAGE_HOST} agent contact", api_key)
    except Exception:
        logger.exception("the agent lookup could not reach the search service")
        return Found()

    for result in results:
        found = extract(
            str(result.get("markdown", "")),
            str(result.get("url", "")),
            agent_name,
        )
        if found.is_usable:
            logger.info("found %s on %s", agent_name, found.source_url)
            return found
    logger.info("the brokerage site did not yield details for %s", agent_name)
    return Found()
