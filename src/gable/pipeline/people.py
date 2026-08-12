"""Working out whose name, face and number belong on a flyer.

Most posts have one agent: whoever submitted the form. Some designs carry two,
and the second one is named in prose in the submission's notes — "Listed by
Stacey Abbott, hosted by Jason Vetter" — with no email to look them up by.

Two rules govern this, and both come from real failures:

* **The submitter is not assumed to hold the listing.** On row 84 the submitter
  is the *hosting* agent and somebody else holds the listing. Putting the
  submitter in the listing slot renders perfectly and is false.
* **A missing co-agent is a question, never a fallback.** Filling both slots
  from the one agent Gable knows produces a flyer showing the same person twice,
  which reads as deliberate and is wrong. Chase's rule: if the information is
  not there, say so.

Does not handle: deciding *which* design to use. That is `slides/selection.py`.
"""

from __future__ import annotations

from sqlite3 import Connection

from gable.listings.address import tidy
from gable.listings.intake import Intake, named_agents, needs_two_agents
from gable.listings.names import tidy_name
from gable.sheets import repository as repo


def announce(connection: Connection, intake: Intake) -> str:
    """How a new submission introduces itself in Slack.

    Args:
        connection: An open database connection, for the roster lookup.
        intake: The submission being started.

    Returns:
        A sentence fragment naming the request type, the agent and the
        property: `New Sold request from Eric Jacobs — 23 Pierside Ave,
        Unit 118, Baltimore, MD 21230`. Each part is dropped rather than
        invented when the form did not supply it.

    Raises:
        Nothing.
    """
    # The roster is the better source for the name: agents type their own
    # inconsistently, and this listing's arrives as "Eric jacobs".
    person = repo.find_salesperson(connection, intake.agent_email)
    roster_name = " ".join(
        part for part in (person.get("first_name", ""), person.get("last_name", "")) if part
    )
    who = tidy_name(roster_name or intake.agent_name)
    kind = intake.request_type.strip()
    headline = f"New {kind} request" if kind else "New request"
    if who:
        headline = f"{headline} from {who}"
    # Tidied for the same reason the flyer tidies it: this is the first thing
    # Carmen reads about the listing, and the form takes whatever an agent
    # types. `tidy` corrects nothing, so it still says what was submitted.
    address = tidy(intake.address)
    return f"{headline} — {address}" if address else headline


def co_agent(connection: Connection, intake: Intake) -> dict[str, str] | None:
    """The second agent on a two-agent post, looked up in the roster.

    Args:
        connection: An open database connection.
        intake: The submission.

    Returns:
        Their roster row, with the name they were called by under `_name`. An
        otherwise-empty dict carrying only `_name` means the notes named them
        and the roster does not have them. None means this is not a two-agent
        post at all.

    Raises:
        Nothing.
    """
    if not needs_two_agents(intake):
        return None
    submitter = intake.agent_name.strip().lower()
    others = [name for name in named_agents(intake).values() if name.strip().lower() != submitter]
    if not others:
        return None
    found = repo.find_salesperson_by_name(connection, others[0])
    return {**found, "_name": others[0]}


def co_agent_values(connection: Connection, intake: Intake) -> dict[str, str]:
    """The co-agent's details, ready to fill a design's second slot.

    Args:
        connection: An open database connection.
        intake: The submission.

    Returns:
        `agent2_*` values, or an empty mapping when this is a single-agent post
        or the roster does not have them. Never falls back to the submitter.

    Raises:
        Nothing.
    """
    person = co_agent(connection, intake)
    if not person or not person.get("phone"):
        return {}
    full = " ".join(
        part for part in (person.get("first_name", ""), person.get("last_name", "")) if part
    )
    return {
        "agent2_name": full or person.get("_name", ""),
        "agent2_phone": person.get("phone", ""),
        "agent2_email": person.get("email", ""),
        "agent2_headshot": person.get("headshot_url", ""),
    }
