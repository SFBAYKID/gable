"""Assemble the exact values one flyer run may place on a template.

This keeps contact fallbacks and template-facing field names in one place.  It
never researches or guesses; callers provide already verified property facts,
and agent data comes only from the local roster mirror.
"""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection
from typing import Final

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
    )
    return kept.strip(",") or square_feet.strip()


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
    return {
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
        **review_values(intake.request_type, intake.post_details or intake.extra_notes),
    }


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
