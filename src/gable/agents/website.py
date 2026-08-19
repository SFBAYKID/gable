"""Validate an agent contact before a listing reaches Slack.

The Drive workbook remains the first source.  When an exact workbook row is
absent or incomplete, or a selected source requires a credential the workbook
does not collect, this module checks the one official Corner House Realty
website for an exact-name profile.  A credential the profile does not state
falls back to the configured brokerage-wide default, which is a fact about the
brokerage rather than a guess about a person; the profile always wins when it
states one, and the two are told apart in the recorded provenance.  A website
value fills that gap for the current run; it never overwrites the workbook,
changes a submitted value, or resolves a conflict by choosing whichever source
looks more plausible.

A *complete* workbook row is also cross-checked against that profile, because a
complete row is not the same as a correct one.  Only the direct phone is
compared, and a disagreement pauses rather than picking a winner; see
`_phone_cross_check` for why the name is deliberately excluded and why an
unreachable site yields to the workbook instead of stopping every listing.

The official site protects email addresses with Cloudflare's small XOR encoding.
That encoding is decoded locally from the profile HTML.  Contact extraction is
limited to the profile's own contact block so an office number in the footer can
never become an agent's direct line.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final

from gable.agents.contacts import Contact

OFFICIAL_HOST: Final[str] = "cornerhouserealty.com"
OFFICIAL_PAGES_API: Final[str] = f"https://{OFFICIAL_HOST}/wp-json/wp/v2/pages"
WORKBOOK_SOURCE: Final[str] = "contact_workbook"
WEBSITE_SOURCE: Final[str] = "official_website"
#: A credential that comes from the configured brokerage-wide default rather
#: than from this agent's own profile. Named separately so a run event says
#: which of the two answered, and so an audit can find every flyer that leaned
#: on the default.
BROKERAGE_SOURCE: Final[str] = "brokerage_default"
_TIMEOUT_SECONDS: Final[int] = 20
#: How many same-named candidate pages are worth reading before giving up. Two
#: is the real case — a profile and its open-houses twin — and the cap bounds a
#: pathological search result rather than fetching the whole site.
_MAX_CANDIDATE_PROFILES: Final[int] = 3
_MAX_RESPONSE_BYTES: Final[int] = 2 * 1024 * 1024
#: Punctuation that can sit at the edge of a written name without being part of
#: it — the comma in "Caleb Olawuyi, Realtor" is the case that reached Carmen.
_EDGE_PUNCTUATION: Final[str] = ",.;:!?()[]{}\"'"
_TITLE_TAG: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")

Fetch = Callable[[str], tuple[bytes, str]]


@dataclass(frozen=True, slots=True)
class OfficialProfile:
    """One exact agent profile read from the official brokerage website."""

    name: str
    email: str
    phone: str
    title: str
    source_url: str


@dataclass(frozen=True, slots=True)
class ProfileLookup:
    """A safe official-site result, including a human-actionable refusal."""

    profile: OfficialProfile | None = None
    problem: str = ""


@dataclass(frozen=True, slots=True)
class ContactCheck:
    """The values and provenance proven before a listing may proceed."""

    name: str = ""
    email: str = ""
    phone: str = ""
    title: str = ""
    name_source: str = ""
    email_source: str = ""
    phone_source: str = ""
    title_source: str = ""
    source_url: str = ""
    problem: str = ""

    @property
    def ready(self) -> bool:
        """Return whether all three contact prerequisites were proven."""
        return bool(self.name and self.email and self.phone and not self.problem)

    def provenance_detail(self) -> str:
        """Return a value-free audit detail suitable for ``run_events``."""
        detail = (
            "contact prerequisites validated: "
            f"name from {self.name_source}, email from {self.email_source}, "
            f"phone from {self.phone_source}"
        )
        return f"{detail}, title from {self.title_source}" if self.title_source else detail


def _name_key(value: str) -> str:
    """Fold insignificant case and spacing while preserving the actual name."""
    return " ".join(value.split()).casefold()


def _name_words(value: str) -> list[str]:
    """Split a name into comparable words, discarding only edge punctuation.

    An agent who appends a credential to their own name writes "Caleb Olawuyi,
    Realtor". The comma belongs to the branding, not to the surname, so a
    prefix test that reads the second word as "olawuyi," can never match the
    official "Caleb Olawuyi" — and Gable had just told Carmen to write the name
    that way. Punctuation inside a word is preserved, so "O'Brien" and
    "Smith-Jones" stay one word each and stay distinct from "OBrien".
    """
    return [word for word in (w.strip(_EDGE_PUNCTUATION) for w in _name_key(value).split()) if word]


def _clean_name(value: str) -> str:
    """Collapse whitespace without changing any submitted spelling."""
    return " ".join(value.split())


def _phone_is_usable(value: str) -> bool:
    """Return whether a phone has the ten North American digits needed for print."""
    return len(_phone_key(value)) == 10


def _phone_key(value: str) -> str:
    """Normalize phone punctuation solely for cross-source comparison."""
    digits = "".join(character for character in value if character.isdigit())
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def _is_branded_form_of(submitted: str, profile_title: str) -> bool:
    """Whether a submitted name is an official name plus a self-branding suffix.

    Agents append a tagline to their own name — "Bobby Carr The Dog Walking
    Realtor" for an official "Bobby Carr". That is branding, not a different
    person, and treating it as a mismatch denied him every design that prints a
    credential.

    The match is a whole-word prefix with a non-empty remainder, so "Bobby Carr"
    can stand in for the branded form while "Bob" never stands in for "Bobby".
    It only nominates a candidate; `lookup_official_profile` still proves
    identity with an email or direct phone from the profile itself.

    Args:
        submitted: The name on the request or the contact workbook.
        profile_title: An official-site page title.

    Returns:
        True when the title is a strict whole-word prefix of the submitted name.

    Raises:
        Nothing.
    """
    submitted_words = _name_words(submitted)
    title_words = _name_words(profile_title)
    if not title_words or not submitted_words or title_words == submitted_words:
        return False
    # Two words is the floor: a lone first name is not enough to nominate a
    # profile, even though the contact-detail check would still have to pass.
    if len(title_words) < 2:
        return False
    return (
        len(submitted_words) > len(title_words)
        and submitted_words[: len(title_words)] == title_words
    )


def _partial_workbook_name_matches(submitted: str, contact: Contact) -> bool:
    """Check any populated workbook name component against the submitted name."""
    submitted_key = _name_key(submitted)
    first_key = _name_key(contact.first_name)
    last_key = _name_key(contact.last_name)
    if first_key and not (submitted_key == first_key or submitted_key.startswith(f"{first_key} ")):
        return False
    return not last_key or submitted_key == last_key or submitted_key.endswith(f" {last_key}")


def _phone_cross_check(
    label: str,
    name: str,
    email: str,
    workbook: Contact,
    official_lookup: Callable[[str, str], ProfileLookup],
) -> str:
    """Compare a complete workbook row's phone against the official profile.

    A complete workbook row used to be trusted outright, so a row that was
    filled in but *wrong* was never questioned. That reached production: the
    row for ``samuel@cornerhouserealty.com`` carried Sam Johnson's name and
    email beside a different agent's direct line, and nothing in the pipeline
    could see it. A missing value stops a run; a confidently wrong one prints.

    Only the phone is compared here. The email is already proven by two earlier
    exact checks — the workbook row's address must equal the submitted one, and
    ``lookup_official_profile`` returns a profile only when that same address
    appears on it — so a returned profile is identity-confirmed. The name is
    deliberately *not* compared: agents brand themselves ("Bobby Carr The Dog
    Walking Realtor" against an official "Bobby Carr"), those variants are not
    errors, and a check that pauses on them trains its reader to ignore it.

    Args:
        label: Human label already chosen for this request's pause messages.
        name: Cleaned agent name submitted on the form.
        email: Lowercased agent email submitted on the form.
        workbook: The exact, complete workbook row for that email.
        official_lookup: Official-domain profile lookup.

    Returns:
        A pause message when the two sources give different direct lines, or
        an empty string when they agree or when no comparison was possible.

    Raises:
        Nothing. Every lookup failure resolves to an empty string.
    """
    try:
        looked_up = official_lookup(name, email)
    except Exception:
        # The workbook remains the designated authority; the website is a
        # cross-check. An unreachable or reshaped site must not halt every
        # listing, so a check that cannot run yields to the workbook.
        return ""
    profile = looked_up.profile
    if profile is None:
        return ""
    if _phone_key(profile.phone) != _phone_key(workbook.phone):
        return _pause(
            label,
            "the official website profile phone does not match the contact-workbook phone.",
        )
    return ""


def _credential_pause(label: str) -> str:
    """Write the one pause a human cannot resolve by editing what they hold.

    The generic contact remedy — correct the request or Agents Contact
    Information — is false for a missing credential, because `validate_contact`
    accepts a title only from the official profile and the roster workbook has
    no title column to put one in. Carmen was told that remedy three times for
    Caleb Olawuyi on 2026-08-19: she answered "Realtor" in the thread, filed
    him in Agents Contact Information, and finally appended the credential to
    his name on the request, which broke the profile match as well. A message
    that names an impossible fix costs more than one that names no fix at all.
    """
    return (
        f"{label} — every contact detail is correct. This design also prints a credential, "
        "and I am only allowed to take that from the agent's profile on the official Corner "
        "House Realty website, where the job-title field is empty. Telling me the credential "
        "here, or filing it in Agents Contact Information, cannot reach me. Once the job "
        "title is on that profile, tell me to run again."
    )


def _pause(label: str, detail: str) -> str:
    """Write one consistent, non-technical contact pause message."""
    return (
        f"{label} — {detail} I left every submitted and filed contact detail unchanged. "
        "Correct the request or Agents Contact Information, then tell me to run again."
    )


def contact_from_record(record: Mapping[str, str]) -> Contact | None:
    """Convert a local roster row to the workbook contact type.

    Args:
        record: Result of ``repository.find_salesperson``.

    Returns:
        A contact when a roster row exists, otherwise ``None``.

    Raises:
        Nothing.
    """
    if not record:
        return None
    return Contact(
        email=str(record.get("email", "")).strip().lower(),
        first_name=str(record.get("first_name", "")).strip(),
        last_name=str(record.get("last_name", "")).strip(),
        phone=str(record.get("phone", "")).strip(),
    )


def validate_contact(
    submitted_name: str,
    submitted_email: str,
    workbook: Contact | None,
    official_lookup: Callable[[str, str], ProfileLookup],
    *,
    require_title: bool = False,
    default_title: str = "",
) -> ContactCheck:
    """Prove name, email, and direct phone before any listing announcement.

    Args:
        submitted_name: Agent name from the read-only form response.
        submitted_email: Agent email from the read-only form response.
        workbook: Exact-email row from Agents Contact Information, if present.
        official_lookup: Official-domain check. It fills a missing or
            incomplete workbook value or a source-required title, and it
            cross-checks the direct phone on an otherwise complete row. It
            never resolves a conflict by choosing a winner.
        require_title: Whether this exact source has an agent-title field.
        default_title: The credential every agent at this brokerage holds, used
            only when the proven profile states no title of its own. Empty
            restores the older rule, under which a blank profile job title
            stops the run.

    Returns:
        Resolved values with field-level provenance, or a precise pause reason.

    Raises:
        Nothing. Lookup failures are normal ``needs_info`` outcomes.
    """
    name = _clean_name(submitted_name)
    email = submitted_email.strip().lower()
    label = name or email or "This request"
    if not name or not email:
        missing = "name" if not name else "email address"
        return ContactCheck(
            problem=_pause(label, f"the request does not include an agent {missing}.")
        )

    workbook_name = ""
    if workbook is not None:
        if workbook.email.strip().lower() != email:
            return ContactCheck(
                problem=_pause(
                    label,
                    "the submitted email address conflicts with the contact-workbook row.",
                )
            )
        workbook_name = _clean_name(f"{workbook.first_name} {workbook.last_name}")
        if workbook_name and _name_key(workbook_name) != _name_key(name):
            # One missing workbook name component is incomplete, not proof of a
            # different person.  A populated component that disagrees is a real
            # conflict and must never be web-corrected.
            incomplete = not (workbook.first_name.strip() and workbook.last_name.strip())
            # A request carrying the filed name plus a suffix is the branding
            # case the official-profile match already allows, and the filed name
            # is what prints. Caleb Olawuyi's request read "Caleb Olawuyi,
            # Realtor" against a filed "Caleb Olawuyi" — same person, same
            # email, and refusing there helps nobody.
            branded = _is_branded_form_of(name, workbook_name)
            if not branded and (
                not incomplete or not _partial_workbook_name_matches(name, workbook)
            ):
                return ContactCheck(
                    problem=_pause(
                        label,
                        "the submitted name does not match the contact-workbook name for "
                        "that email address.",
                    )
                )

    name_ready = bool(
        workbook_name
        and (
            _name_key(workbook_name) == _name_key(name) or _is_branded_form_of(name, workbook_name)
        )
    )
    email_ready = workbook is not None
    phone_ready = bool(workbook is not None and _phone_is_usable(workbook.phone))
    if name_ready and email_ready and phone_ready and workbook is not None and not require_title:
        # A complete row is still cross-checked against the official profile
        # before it is trusted. See `_phone_cross_check`: strict on the direct
        # line, silent about name variants, and yielding to the workbook when
        # the site cannot answer.
        conflict = _phone_cross_check(label, name, email, workbook, official_lookup)
        if conflict:
            return ContactCheck(problem=conflict)
        return ContactCheck(
            name=workbook_name,
            email=email,
            phone=workbook.phone.strip(),
            name_source=WORKBOOK_SOURCE,
            email_source=WORKBOOK_SOURCE,
            phone_source=WORKBOOK_SOURCE,
        )

    try:
        looked_up = official_lookup(name, email)
    except Exception:
        looked_up = ProfileLookup(
            problem=(
                "I could not complete the check against the official Corner House Realty website"
            )
        )
    profile = looked_up.profile
    if profile is None:
        detail = looked_up.problem or (
            "I could not find one exact agent profile on the official Corner House Realty website"
        )
        return ContactCheck(problem=_pause(label, f"{detail}."))
    # `lookup_official_profile` has already proven this profile belongs to this
    # agent, by their email or their filed direct phone. Re-refusing on the name
    # would undo that: the site says "Bobby Carr" and he brands himself "Bobby
    # Carr The Dog Walking Realtor", and neither is wrong.
    if _name_key(profile.name) != _name_key(name) and not _is_branded_form_of(name, profile.name):
        return ContactCheck(
            problem=_pause(
                label,
                "the official website profile name does not match the submitted name.",
            )
        )
    if profile.email.strip().lower() != email:
        return ContactCheck(
            problem=_pause(
                label,
                "the official website profile email does not match the submitted email address.",
            )
        )
    if not _phone_is_usable(profile.phone):
        return ContactCheck(
            problem=_pause(label, "the official website profile has no usable direct phone number.")
        )
    if (
        phone_ready
        and workbook is not None
        and _phone_key(workbook.phone) != _phone_key(profile.phone)
    ):
        return ContactCheck(
            problem=_pause(
                label,
                "the official website profile phone does not match the contact-workbook phone.",
            )
        )
    # The profile's own title always wins; the brokerage default only fills a
    # blank one. Chase confirmed on 2026-08-19 that all 38 agents on the roster
    # hold the credential, which is what makes a default honest here: it states
    # a fact about the brokerage rather than guessing about a person.
    resolved_title = profile.title.strip() or default_title.strip()
    if require_title and not resolved_title:
        return ContactCheck(problem=_credential_pause(label))

    # Website evidence is allowed to fill only what the exact workbook row did
    # not establish.  Existing workbook values are retained byte-for-byte.
    return ContactCheck(
        name=workbook_name if name_ready else profile.name,
        email=email,
        phone=workbook.phone.strip() if phone_ready and workbook is not None else profile.phone,
        title=resolved_title if require_title else "",
        name_source=WORKBOOK_SOURCE if name_ready else WEBSITE_SOURCE,
        email_source=WORKBOOK_SOURCE if email_ready else WEBSITE_SOURCE,
        phone_source=WORKBOOK_SOURCE if phone_ready else WEBSITE_SOURCE,
        title_source=(
            (WEBSITE_SOURCE if profile.title.strip() else BROKERAGE_SOURCE) if require_title else ""
        ),
        source_url=profile.source_url,
    )


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
    except (UnicodeDecodeError, ValueError):
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
    return _clean_name(html.unescape(_TITLE_TAG.sub("", rendered)))


def lookup_official_profile(
    agent_name: str,
    agent_email: str,
    known_phone: str = "",
    *,
    fetch: Fetch = _fetch,
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

    Returns:
        One exact, official-domain profile or a plain-language refusal.

    Raises:
        Nothing. Network and parsing failures become a safe lookup problem.
    """
    name = _clean_name(agent_name)
    email_address = agent_email.strip().lower()
    if not name or not email_address:
        return ProfileLookup(problem="the request does not identify one agent by name and email")
    try:

        def _titles(term: str) -> list[tuple[str, str]]:
            """Every official-domain page title the site returns for one term."""
            query = urllib.parse.urlencode(
                {"search": term, "per_page": "20", "_fields": "link,title"}
            )
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
                problem=(
                    "the official Corner House Realty website has no exact profile for this agent"
                )
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
            titles = _unique([_clean_name(value) for value in parser.title_parts])
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
                )
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
    except Exception:
        return ProfileLookup(
            problem=(
                "I could not complete the check against the official Corner House Realty website"
            )
        )
