"""Getting one brokerage page and reading a profile out of its HTML.

Page mechanics only: the URL rule, the bounded fetch, Cloudflare's email
obfuscation, and the parser that walks the markup. Nothing here decides which
page is the right one or what it proves about an agent — `website.py` owns
that, and it is the module to read first.

The two halves fail differently, which is why they are apart. This one fails on
network shapes and markup that moved; the other fails on whether a name, a
phone and a credential agree well enough to put somebody's details on a real
client's flyer.

Assumes: the brokerage runs WordPress and exposes `wp-json/wp/v2/pages`.
Confirmed live 2026-08-19.

Does not handle: choosing among candidate pages, or judging a profile.
"""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from html.parser import HTMLParser
from typing import Final

from gable.agents.names import clean_name

OFFICIAL_HOST: Final[str] = "cornerhouserealty.com"
OFFICIAL_PAGES_API: Final[str] = f"https://{OFFICIAL_HOST}/wp-json/wp/v2/pages"
_TIMEOUT_SECONDS: Final[int] = 20
_MAX_RESPONSE_BYTES: Final[int] = 2 * 1024 * 1024
_TITLE_TAG: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")

Fetch = Callable[[str], tuple[bytes, str]]


def _official_url(value: str) -> bool:
    """Return whether a URL is HTTPS on the one approved brokerage domain."""
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host.removeprefix("www.") == OFFICIAL_HOST


def _fetch(url: str) -> tuple[bytes, str]:
    """Fetch one bounded official-site response with an explicit timeout."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/html",
            "User-Agent": "Gable/1.0 contact prerequisite check",
        },
    )
    # urllib contract: https://docs.python.org/3/library/urllib.request.html#urllib.request.urlopen
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        final_url = str(response.geturl())
        if not _official_url(final_url):
            raise ValueError("the official-site request redirected off the approved domain")
        data = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(data) > _MAX_RESPONSE_BYTES:
        raise ValueError("the official-site response was unexpectedly large")
    return data, final_url


def _decode_cloudflare_email(encoded: str) -> str:
    """Decode one Cloudflare ``data-cfemail`` value, or return empty."""
    try:
        raw = bytes.fromhex(encoded)
        if len(raw) < 2:
            return ""
        key = raw[0]
        return bytes(value ^ key for value in raw[1:]).decode("utf-8").strip().lower()
    except (
        UnicodeDecodeError,
        ValueError,
    ):  # silent: a malformed encoding decodes to no email
        return ""


class _ProfileParser(HTMLParser):
    """Extract only the agent profile's contact dropdown, excluding the footer."""

    def __init__(self) -> None:
        """Create an empty, depth-aware profile parser."""
        super().__init__(convert_charrefs=True)
        self._div_depth = 0
        self._contact_depth = 0
        self.emails: list[str] = []
        self.phones: list[str] = []
        self.title_parts: list[str] = []
        self._title_depth = 0
        self._job_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Collect email and phone attributes inside the profile contact block."""
        attributes = dict(attrs)
        if tag == "div":
            self._div_depth += 1
            classes = set((attributes.get("class") or "").split())
            if "contact-button__dropdown" in classes and not self._contact_depth:
                self._contact_depth = self._div_depth
            if "cbl__widget--job_title" in classes and not self._job_depth:
                self._job_depth = self._div_depth
            if self._job_depth and "cb-title" in classes and not self._title_depth:
                self._title_depth = self._div_depth
        if not self._contact_depth:
            return
        href = html.unescape(attributes.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            email = urllib.parse.unquote(href[7:]).split("?", 1)[0].strip().lower()
            if email:
                self.emails.append(email)
        if href.lower().startswith("tel:"):
            phone = urllib.parse.unquote(href[4:]).strip()
            if phone:
                self.phones.append(phone)
        encoded = attributes.get("data-cfemail") or ""
        decoded = _decode_cloudflare_email(encoded)
        if decoded:
            self.emails.append(decoded)

    def handle_endtag(self, tag: str) -> None:
        """Leave the contact scope at the matching closing div."""
        if tag != "div":
            return
        if self._contact_depth == self._div_depth:
            self._contact_depth = 0
        if self._title_depth == self._div_depth:
            self._title_depth = 0
        if self._job_depth == self._div_depth:
            self._job_depth = 0
        self._div_depth = max(0, self._div_depth - 1)

    def handle_data(self, data: str) -> None:
        """Collect visible title text only inside the profile title element."""
        if self._title_depth and data.strip():
            self.title_parts.append(data.strip())


def _unique(values: list[str]) -> list[str]:
    """Return non-empty values once, preserving source order."""
    return list(dict.fromkeys(value for value in values if value))


def _title(value: object) -> str:
    """Read a WordPress rendered title as plain text."""
    if not isinstance(value, Mapping):
        return ""
    rendered = value.get("rendered", "")
    if not isinstance(rendered, str):
        return ""
    return clean_name(html.unescape(_TITLE_TAG.sub("", rendered)))
