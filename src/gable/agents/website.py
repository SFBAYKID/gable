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

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from gable.agents.contacts import Contact
from gable.agents.names import clean_name

WORKBOOK_SOURCE: Final[str] = "contact_workbook"
WEBSITE_SOURCE: Final[str] = "official_website"
#: A credential that comes from the configured brokerage-wide default rather
#: than from this agent's own profile. Named separately so a run event says
#: which of the two answered, and so an audit can find every flyer that leaned
#: on the default.
BROKERAGE_SOURCE: Final[str] = "brokerage_default"
#: Punctuation that can sit at the edge of a written name without being part of
#: it — the comma in "Caleb Olawuyi, Realtor" is the case that reached Carmen.
_EDGE_PUNCTUATION: Final[str] = ",.;:!?()[]{}\"'"

logger = logging.getLogger("gable.agents.website")


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
    #: A profile carrying this agent's name was found, but nothing on it matched
    #: the request. That is the signal that the address on the request is not
    #: this agent's — not that the website is missing them — and it reads very
    #: differently to whoever has to fix it.
    found_but_unproven: bool = False
    #: The site did not answer — a timeout, a connection failure, a malformed
    #: response — so nothing is known either way. Distinct from a site that
    #: answered "no such agent": that is evidence about the request, this is
    #: not. On 2026-09-01 one twenty-second timeout on a recheck told Carmen to
    #: "correct the request or Agents Contact Information" for an agent whose
    #: request and roster row were both fine.
    unavailable: bool = False


#: What the official site's silence is called in a pause or a delivery note.
SITE_UNAVAILABLE: Final[str] = (
    "I could not complete the check against the official Corner House Realty website"
)


def unavailable_lookup() -> ProfileLookup:
    """The one result every network or parsing failure resolves to.

    Returns:
        A `ProfileLookup` naming the site's silence, marked `unavailable` so
        the caller can tell it from an answer.

    Raises:
        Nothing.
    """
    return ProfileLookup(problem=SITE_UNAVAILABLE, unavailable=True)


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
    #: Something the person should hear once the flyer is delivered, in
    #: Carmen's words. Today it is one sentence: the credential came from the
    #: brokerage default because the site could not be read for this run.
    note: str = ""

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
    except Exception as error:
        logger.warning(
            "the official profile cross-check for %s raised %s; the workbook stands",
            email,
            type(error).__name__,
        )
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


def _unavailable_pause(label: str, row_complete: bool, require_title: bool) -> str:
    """Say the site did not answer, and name a remedy that can actually work.

    The generic remedy sends a person to correct the request or the roster.
    When the site simply did not answer, neither is wrong, and Carmen followed
    that instruction on 2026-09-01 for Brittney Bushee — "her contact info is
    on the spreadsheet" — with nothing to correct. What was wanted from the
    site is named, and the remedy is the true one: try again.

    Args:
        label: The agent's name, or the request's label when there is none.
        row_complete: Whether the filed roster row already proves every
            contact detail, so the site was only wanted for a credential.
        require_title: Whether the selected design prints a credential.

    Returns:
        One pause message in Carmen's words.

    Raises:
        Nothing.
    """
    if row_complete and require_title:
        wanted = "the credential this design prints"
    elif row_complete:
        wanted = "a cross-check of the filed phone number"
    else:
        wanted = "the contact details the roster does not carry for this agent"
    return (
        f"{label} — the official Corner House Realty website did not answer when I looked "
        f"for the profile, and I needed it for {wanted}. Nothing about the request or Agents "
        "Contact Information needs changing. Tell me to run again and I will check it again."
    )


def _site_silent_note(label: str, credential: str) -> str:
    """The delivery sentence for a credential the site could not be asked about.

    Args:
        label: The agent's name.
        credential: The brokerage-wide credential that was printed.

    Returns:
        One sentence for the delivery message.

    Raises:
        Nothing.
    """
    return (
        f"The official Corner House Realty website did not answer when I checked {label}'s "
        f"profile, so the credential on the flyer is the brokerage's {credential} rather than "
        "one read from that page."
    )


def names_agree(submitted: str, contact: Contact) -> bool:
    """Whether a filed roster row is the agent this request names.

    Args:
        submitted: The agent name on the request.
        contact: One roster row.

    Returns:
        True when the filed name is the submitted one, or the submitted name is
        the filed one plus a branding suffix.

    Raises:
        Nothing.
    """
    filed = clean_name(f"{contact.first_name} {contact.last_name}")
    if not filed or not submitted.strip():
        return False
    return _name_key(filed) == _name_key(submitted) or _is_branded_form_of(submitted, filed)


def unidentified_pause(name: str, roster_size: int = 0) -> str:
    """Say that nothing on the request establishes which agent it is for.

    The form's email field holds whoever filled the form in. On 2026-08-19 one
    person submitted two requests for two other agents, so that address proved
    nothing about either. When it is not the named agent's and the roster has
    no row for that name either, there is no evidence left, and picking a
    same-named profile off the website would be guessing whose phone number
    goes on a client's flyer.

    Args:
        name: The agent named on the request.
        roster_size: How many agents the read that just happened returned,
            spoken so that a repeat is visibly a fresh check.

    Returns:
        The sentence to post.

    Raises:
        Nothing.

    Note:
        Halim Joseph's request was refused four times in identical words on
        2026-08-28. Every refusal was TRUE — he reached Agents Contact
        Information at 13:48:48 and the last refusal went out at 13:47:16 — but
        Carmen had answered "I fixed that. Please run again." and got the same
        sentence back, so she could not tell a fresh read from a stored one. She
        gave up ninety-two seconds before it would have worked. Saying what the
        read returned is what makes the difference visible: "40 agents" tells
        her the edit has not landed, which is the fact she actually needed.
    """
    read = (
        f"I read Agents Contact Information again just now — {roster_size} agents — "
        f"and there is still no row for {name}. "
        if roster_size
        else f"There is no row for {name} in Agents Contact Information. "
    )
    return (
        f"{name} — the email on this request belongs to whoever submitted the form rather "
        f"than to {name}. {read}"
        "I have nothing that proves which agent this is for. Add them to Agents Contact "
        "Information, then tell me to run again."
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
    name = clean_name(submitted_name)
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
        workbook_name = clean_name(f"{workbook.first_name} {workbook.last_name}")
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
        logger.exception("the official profile lookup for %s raised", email)
        looked_up = unavailable_lookup()
    profile = looked_up.profile
    if profile is None:
        row_complete = name_ready and email_ready and phone_ready and workbook is not None
        if (
            looked_up.unavailable
            and row_complete
            and workbook is not None
            and require_title
            and default_title.strip()
        ):
            # The site's silence is not evidence about this agent. Every contact
            # detail is already proven from the filed row, and the only thing
            # the profile was wanted for is a credential that Chase settled on
            # 2026-08-19 as a fact about the whole brokerage. Stopping here
            # sent Carmen to correct a request and a roster row that were both
            # right; the flyer goes out with the brokerage credential and says
            # so, and the provenance records which source answered.
            return ContactCheck(
                name=workbook_name,
                email=email,
                phone=workbook.phone.strip(),
                title=default_title.strip(),
                name_source=WORKBOOK_SOURCE,
                email_source=WORKBOOK_SOURCE,
                phone_source=WORKBOOK_SOURCE,
                title_source=BROKERAGE_SOURCE,
                note=_site_silent_note(label, default_title.strip()),
            )
        if looked_up.unavailable:
            return ContactCheck(problem=_unavailable_pause(label, row_complete, require_title))
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
