"""Name a new submission truthfully in Slack.

The roster is the canonical source for the submitting agent's display name;
the form remains the fallback. Two-agent role parsing lives in the intake and
orchestrator modules, while the runner currently stops those requests until a
template has an explicit, certified slot contract.
"""

from __future__ import annotations

from sqlite3 import Connection

from gable.listings.address import tidy
from gable.listings.intake import Intake
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
