"""Assemble the exact values one flyer run may place on a template.

This keeps contact fallbacks and template-facing field names in one place.  It
never researches or guesses; callers provide already verified property facts,
and agent data comes only from the local roster mirror.
"""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection
from typing import Final

from gable.db import store
from gable.listings.intake import Intake
from gable.listings.review import review_values
from gable.sheets import repository as repo
from gable.slides.manifest import normalise_address

DEFAULT_BROKERAGE_URL: Final[str] = "cornerhouserealty.com"
OFFICE_PHONE: Final[str] = "443.499.3839"
DEFAULT_SOCIAL_HANDLE: Final[str] = ""


def _city_of(address: str) -> str:
    """Return the city from a conventional street, city, state address."""
    parts = [part.strip() for part in address.split(",") if part.strip()]
    return parts[1] if len(parts) >= 2 else ""


def _measure_only(square_feet: str) -> str:
    """Return just the number, because the design draws its own unit.

    Every design puts square footage beside a ft² icon and its own "Sq FT"
    label, so a value carrying the unit renders as "1450 sq ft" next to a
    square-foot symbol. A person answering Gable's question types the unit
    naturally — "1450 sq ft" — and that is not something to correct back at
    them, so it is normalised here instead.

    Args:
        square_feet: The value as supplied or researched.

    Returns:
        The digits and separators only, or the original text when it contains
        no digits at all — better to show what someone typed than nothing.

    Raises:
        Nothing.
    """
    kept = "".join(
        character for character in square_feet if character.isdigit() or character == ","
    ).strip(",")
    if not kept:
        return square_feet.strip()
    # Every design writes its own sample with thousands separators — "6,348
    # SQFT", "2,430 Sq FT" — and a researched figure arrives as bare digits, so
    # a real listing rendered "3663 SQFT" beside a sample that reads 6,348.
    # Grouping a number is presentation, not a change to what anyone stated.
    digits = kept.replace(",", "")
    return f"{int(digits):,}" if digits.isdigit() else kept


#: Longer than this and what the agent typed is marketing prose, not a note
#: about the deal. Under Contract's callout panel is 160x99pt: a sentence fits
#: there, a paragraph would be shrunk to something nobody can read.
_MAX_NOTE_CHARS: Final[int] = 120

#: What agents type into the details column when they have nothing to say.
_EMPTY_NOTES: Final[frozenset[str]] = frozenset({"na", "n/a", "none", "no", "nothing", "-"})


def _listing_note(intake: Intake) -> str:
    """The submission's own short note about the deal, or nothing.

    Only a design that draws a note panel has anywhere to put this, and only
    `slides.fields` decides which designs those are. This just says whether the
    agent wrote something worth printing.

    Args:
        intake: The parsed row.

    Returns:
        The note as one line, or empty when the agent left the column blank,
        dismissed it, or wrote a full marketing paragraph. A review's prose is
        never a note — it is the review, and `review_values` owns it.

    Raises:
        Nothing.
    """
    if "review" in intake.request_type.lower():
        return ""
    note = " ".join(intake.post_details.split())
    if not note or note.strip(" .").casefold() in _EMPTY_NOTES:
        return ""
    return note if len(note) <= _MAX_NOTE_CHARS else ""


def _title_word(title: str) -> str:
    """Return an agent title without its credential mark.

    Args:
        title: The proven title, e.g. `REALTOR®` or
            `REALTOR®, The Kulnich Home Team`.

    Returns:
        The same title with the registered and trademark symbols removed and
        surrounding space tidied. Nothing else is dropped: a title naming a team
        keeps the team, because that is what the agent's own profile says.

    Raises:
        Nothing.
    """
    stripped = title.replace("®", "").replace("™", "")
    return " ".join(stripped.split())


def for_intake(
    connection: Connection,
    intake: Intake,
    known: dict[str, str],
) -> dict[str, str]:
    """Return every text or image value this run can truthfully supply."""
    person = repo.find_salesperson(connection, intake.agent_email)
    name = " ".join(
        part for part in (person.get("first_name", ""), person.get("last_name", "")) if part
    )
    values = {
        "address": intake.address,
        # A public list price is not a Sold closing price or a Price Reduction's
        # new price.  Those request types may use only the form-owned value.
        "price": intake.price
        or (known.get("list_price", "") if intake.accepts_public_list_price else ""),
        "beds": known.get("beds", ""),
        "baths": known.get("baths", ""),
        "square_feet": _measure_only(known.get("square_feet", "")),
        "agent_name": name or intake.agent_name,
        # A missing direct line stays missing. Preflight asks when the selected
        # design has a phone field; silently substituting the brokerage office
        # number makes a plausible flyer with the wrong contact path.
        "agent_phone": person.get("phone", ""),
        "agent_email": intake.agent_email,
        "open_house": intake.open_house,
        "website": person.get("brokerage_url", "") or DEFAULT_BROKERAGE_URL,
        "headshot": person.get("headshot_url", ""),
        # REALTOR is a membership credential, not a generic synonym for agent.
        # Neither current source records a title, so a design that needs one
        # must ask instead of printing a plausible professional claim.
        "agent_title": "",
        "social_handle": DEFAULT_SOCIAL_HANDLE,
        "neighborhood": _city_of(intake.address),
        "listing_note": _listing_note(intake),
        **review_values(intake.request_type, intake.post_details or intake.extra_notes),
    }
    # A pull-quote a person sent back after Gable said the review would not be
    # readable at that length. Every real review on the form runs 400 to 1000
    # characters against a panel drawn for about 280, so the shorter version
    # somebody actually chose outranks the pasted one. Only the quote: the
    # reviewer's name is not theirs to change here.
    stated = store.recall_supplied_facts(connection, intake.address)
    named = stated.get("client_name", "").strip()
    if named:
        values["client_name"] = named
    shorter = stated.get("review_quote", "").strip()
    if shorter and values.get("client_name", "").strip():
        values["review_quote"] = shorter
    return values


def output_name(intake: Intake) -> str:
    """Return the scan-friendly name used for the copied Slides file."""
    return f"{intake.category} — {intake.address} — {intake.agent_name}".strip(" —")


def assembled(
    connection: Connection,
    intake: Intake,
    known: dict[str, str],
    *,
    agent_name: str,
    agent_email: str,
    agent_phone: str,
    agent_title: str,
    hero_photo_url: str,
    headshot_for: Callable[[str], str],
) -> dict[str, str]:
    """Every value one run may place, with the proven contact and images.

    Args:
        connection: An open database connection, for the roster mirror.
        intake: The parsed row.
        known: Property facts already verified for this address.
        agent_name: Name proven by the prerequisite contact check.
        agent_email: Proven email.
        agent_phone: Proven direct line.
        agent_title: Proven title, empty when the design does not show one.
        hero_photo_url: The supplied property photograph, empty until it lands.
        headshot_for: Resolves an agent name to a published portrait URL, or
            empty when the roster has no unambiguous match.

    Returns:
        The complete value map. The contact block overrides the row because the
        agent typed their own details into a form and the roster is canonical.

    Raises:
        sqlite3.Error: on a roster query failure.
    """
    values = for_intake(connection, intake, known)
    values.update(
        {
            "agent_name": agent_name,
            "agent_email": agent_email,
            "agent_phone": agent_phone,
            # Without its credential mark. Every design already sets one
            # exactly where it wants it — New Listing draws a superscript ®
            # beside REALTOR, Under Contract prints none — so supplying the
            # symbol produced "Realtor® ®" on the first and added a mark the
            # second never had. The rest of the title is passed through as the
            # profile states it: "REALTOR®, The Kulnich Home Team" becomes
            # "REALTOR, The Kulnich Home Team", which is why a long title can
            # be too wide for a slot drawn for one word.
            "agent_title": _title_word(agent_title),
        }
    )
    values["address"] = normalise_address(values.get("address", ""))
    values["hero_photo"] = hero_photo_url
    if not values.get("headshot"):
        values["headshot"] = headshot_for(values.get("agent_name", ""))
    return values
