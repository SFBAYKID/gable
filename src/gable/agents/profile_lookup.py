"""Reading one agent's profile off the official Corner House Realty website.

Split from `agents.website` on 2026-09-01 when that module crossed the 800-line
ceiling: this file reads the site, that one decides what the reading proves.
The name locates a candidate page, a contact detail on the page proves it is
this agent, and a transient network failure is tried once more before it is
reported as the site's silence.

Does not handle: deciding whether a profile satisfies a listing's contact
prerequisites, or wording a pause — both stay in `agents.website`.
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Final

from gable.agents.names import clean_name
from gable.agents.profile_page import (
    OFFICIAL_PAGES_API,
    Fetch,
    _fetch,
    _official_url,
    _ProfileParser,
    _title,
    _unique,
)
from gable.agents.website import (
    OfficialProfile,
    ProfileLookup,
    _is_branded_form_of,
    _name_key,
    _name_words,
    _phone_is_usable,
    _phone_key,
    unavailable_lookup,
)

#: How many same-named candidate pages are worth reading before giving up. Two
#: is the real case — a profile and its open-houses twin — and the cap bounds a
#: pathological search result rather than fetching the whole site.
_MAX_CANDIDATE_PROFILES: Final[int] = 3
#: One more attempt after a transient network failure, and the jittered pause
#: before it. Brittney Bushee's recheck on 2026-09-01 lost a whole listing to a
#: single twenty-second timeout on a site that answered in under a second when
#: tried again; two attempts bound the cost at well under a minute.
_LOOKUP_ATTEMPTS: Final[int] = 2
_RETRY_PAUSE_SECONDS: Final[tuple[float, float]] = (0.5, 1.5)

logger = logging.getLogger("gable.agents.profile_lookup")


def _is_transient(error: Exception) -> bool:
    """Whether a lookup failure is worth one more attempt.

    Args:
        error: What the attempt raised.

    Returns:
        True for a timeout, a dropped connection, or a server-side HTTP error.
        False for a client-side HTTP error, an off-domain redirect, or a
        response that could not be parsed — asking again gets the same answer.

    Raises:
        Nothing.
    """
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500
    # `URLError` wraps socket failures and is itself an `OSError`; so is a
    # bare socket timeout.
    return isinstance(error, OSError | TimeoutError)


def lookup_official_profile(
    agent_name: str,
    agent_email: str,
    known_phone: str = "",
    *,
    fetch: Fetch = _fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> ProfileLookup:
    """Find one official profile and prove it belongs to this agent.

    The name locates a candidate — exactly, or allowing a self-branding suffix
    such as "Bobby Carr The Dog Walking Realtor" for an official "Bobby Carr".
    Identity is then proven by a contact detail on the profile itself: the
    submitted email, or the filed direct phone when the agent submits from a
    personal address the brokerage page does not list.

    Args:
        agent_name: Name submitted on the request form.
        agent_email: Email submitted on the request form.
        known_phone: The contact workbook's direct phone for this agent, used
            as the identity proof when the profile does not show their email.
            Empty means email is the only accepted proof.
        fetch: Bounded HTTP seam, injectable for hermetic tests.
        sleep: The pause before the one retry, injectable so a test does not
            wait.

    Returns:
        One exact, official-domain profile or a plain-language refusal.

    Raises:
        Nothing. Network and parsing failures become a safe lookup problem,
        marked `unavailable`, after one bounded retry of a transient one.
    """
    name = clean_name(agent_name)
    email_address = agent_email.strip().lower()
    if not name or not email_address:
        return ProfileLookup(problem="the request does not identify one agent by name and email")
    for attempt in range(1, _LOOKUP_ATTEMPTS + 1):
        try:
            return _read_official_profile(name, email_address, known_phone, fetch)
        except Exception as error:
            # The cause, for whoever has to fix it. This used to be swallowed
            # whole, so a timeout and a bug read identically in Slack and left
            # nothing at all in the log.
            logger.warning(
                "official profile lookup for %s failed on attempt %d: %s",
                email_address,
                attempt,
                type(error).__name__,
            )
            if attempt < _LOOKUP_ATTEMPTS and _is_transient(error):
                sleep(random.uniform(*_RETRY_PAUSE_SECONDS))
                continue
            return unavailable_lookup()
    return unavailable_lookup()


def _read_official_profile(
    name: str,
    email_address: str,
    known_phone: str,
    fetch: Fetch,
) -> ProfileLookup:
    """One attempt at `lookup_official_profile`; raises on any failure.

    Args:
        name: The cleaned agent name.
        email_address: The lower-cased submitted email.
        known_phone: The filed direct phone, or "".
        fetch: Bounded HTTP seam.

    Returns:
        The profile, or a refusal the site itself supports.

    Raises:
        Exception: Anything the fetch or the parse raises, for the caller to
            classify and retry once.
    """

    def _titles(term: str) -> list[tuple[str, str]]:
        """Every official-domain page title the site returns for one term."""
        query = urllib.parse.urlencode({"search": term, "per_page": "20", "_fields": "link,title"})
        raw, final_api_url = fetch(f"{OFFICIAL_PAGES_API}?{query}")
        if not _official_url(final_api_url):
            raise ValueError("the page search left the official domain")
        payload: object = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("the page search did not return a list")
        found: list[tuple[str, str]] = []
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            link = item.get("link", "")
            if isinstance(link, str) and _official_url(link):
                found.append((link, _title(item.get("title"))))
        return found

    # WordPress requires every search term to match, so "Bobby Carr The Dog
    # Walking Realtor" returns nothing at all while "Bobby Carr" returns his
    # profile. A branded name therefore needs a second, shorter query before
    # there is anything to match against.
    results = _titles(name)
    words = _name_words(name)
    if not results and len(words) > 2:
        # The retry drops the branding and any punctuation attached to it.
        # "Caleb Olawuyi, Realtor" searched whole returns nothing at all.
        results = _titles(" ".join(words[:2]))

    exact: list[tuple[str, str]] = []
    branded: list[tuple[str, str]] = []
    for link, title in results:
        if _name_key(title) == _name_key(name):
            exact.append((link, title))
        elif _is_branded_form_of(name, title):
            branded.append((link, title))
    # An agent who brands themselves — "Bobby Carr The Dog Walking Realtor"
    # against an official "Bobby Carr" — has no exact profile, and refusing
    # there denied him every design that prints a credential. The branded
    # form is only ever a fallback for finding a candidate; identity is
    # still proven below by a contact detail, never by the name.
    candidates = list(dict.fromkeys(exact or branded))
    if not candidates:
        return ProfileLookup(
            problem=("the official Corner House Realty website has no exact profile for this agent")
        )

    # One agent can hold several pages under the identical title: Melanie
    # Humeniuk's profile and her open-houses page are both called exactly
    # "Melanie Humeniuk", and refusing on the count alone denied her every
    # design that prints a credential. The name nominates here too, and the
    # contact detail decides — read each candidate and keep the ones that
    # actually carry this agent's email or filed direct phone.
    proven: list[OfficialProfile] = []
    for profile_url, profile_name in candidates[:_MAX_CANDIDATE_PROFILES]:
        profile_html, final_profile_url = fetch(profile_url)
        if not _official_url(final_profile_url):
            raise ValueError("the profile left the official domain")
        parser = _ProfileParser()
        parser.feed(profile_html.decode("utf-8", errors="replace"))
        emails = _unique([value.lower() for value in parser.emails])
        phones = _unique(parser.phones)
        titles = _unique([clean_name(value) for value in parser.title_parts])
        if len(phones) != 1 or not _phone_is_usable(phones[0]):
            continue
        # The email is the usual proof; the filed direct phone is the
        # fallback for an agent who submits from a personal address the
        # brokerage page does not list, which is how Bobby Carr's gmail
        # failed against his official profile. A page matching on neither is
        # somebody else, whatever it is called.
        if email_address not in emails and (
            not known_phone or _phone_key(known_phone) != _phone_key(phones[0])
        ):
            continue
        proven.append(
            OfficialProfile(
                name=profile_name,
                email=email_address,
                phone=phones[0],
                title=titles[0] if len(titles) == 1 else "",
                source_url=final_profile_url,
            )
        )
    if not proven:
        return ProfileLookup(
            problem=(
                "the official profile does not show the submitted email address "
                "or the filed direct phone"
            ),
            found_but_unproven=True,
        )
    # Duplicate pages for one person agree with each other. Pages that
    # disagree on the direct line are not one person, and choosing between
    # them is the guess this module exists to refuse.
    if len({_phone_key(found.phone) for found in proven}) > 1:
        return ProfileLookup(
            problem=(
                "the official Corner House Realty website shows more than one direct "
                "phone number for this agent"
            )
        )
    # Prefer a page carrying a credential: the open-houses twin usually has
    # none, and an empty title would be reported as a missing credential.
    titled = next((found for found in proven if found.title), None)
    return ProfileLookup(profile=titled or proven[0])
