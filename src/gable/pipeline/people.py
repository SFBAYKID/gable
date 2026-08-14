"""Name a new submission truthfully in Slack.

The roster is the canonical source for the submitting agent's display name;
the form remains the fallback. Two-agent role parsing lives in the intake and
orchestrator modules, while the runner currently stops those requests until a
template has an explicit, certified slot contract.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from sqlite3 import Connection

from gable.db import store
from gable.listings.address import tidy
from gable.listings.intake import Intake
from gable.listings.names import tidy_name
from gable.sheets import repository as repo
from gable.voice import safe

logger = logging.getLogger("gable.people")


def opening_for(
    connection: Connection,
    intake: Intake,
    existing_thread_ts: str,
    validated_name: str = "",
) -> str:
    """The announcement a pause posts before its question, if it needs one.

    Every pause announces the listing and then asks inside that thread. Only
    the photo request used to do this, so a research gap or a contact problem
    posted a bare sentence at channel level — "I could not find the square
    feet, list price for this one. Do you have them?" sitting alone in the
    channel, naming no listing. A question with no visible subject cannot be
    answered by anyone who was not already watching.

    Args:
        connection: An open database connection, for the roster lookup.
        intake: The submission being paused.
        existing_thread_ts: The run's current Slack thread root, if it has one.
        validated_name: Name already proven by the contact check, when the
            caller has run it. Empty for the earliest pauses, which are the
            contact failures themselves.

    Returns:
        The announcement, or empty when the run already owns a thread. The
        question store refuses a headline that would replace an existing root,
        and a thread already about this listing needs no second introduction.

    Raises:
        Nothing.
    """
    if existing_thread_ts:
        return ""
    # The submitted name stands in until the contact check proves one, because
    # the earliest pauses are the contact failures themselves.
    return announce(connection, intake, validated_name)


def announce(connection: Connection, intake: Intake, validated_name: str = "") -> str:
    """How a new submission introduces itself in Slack.

    Args:
        connection: An open database connection, for the roster lookup.
        intake: The submission being started.
        validated_name: Name already proven by the prerequisite contact check.
            This matters when an official profile safely supplied a workbook
            blank for the current run without writing into the human-owned file.

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
    who = tidy_name(validated_name or roster_name or intake.agent_name)
    kind = intake.request_type.strip()
    # Several request types already begin with "New" — "New Listing", "New
    # Listing with Open House" — and prefixing blindly produced "New New Listing
    # request from Lolo Simmons", which is the first thing Carmen reads about a
    # listing. The type keeps its own wording when it already opens with it.
    if not kind:
        headline = "New request"
    elif kind.casefold().startswith("new "):
        headline = f"{kind} request"
    else:
        headline = f"New {kind} request"
    if who:
        headline = f"{headline} from {who}"
    # Tidied for the same reason the flyer tidies it: this is the first thing
    # Carmen reads about the listing, and the form takes whatever an agent
    # types. `tidy` corrects nothing, so it still says what was submitted.
    address = tidy(intake.address)
    return f"{headline} — {address}" if address else headline


def open_thread(
    connection: Connection,
    run_id: str,
    intake: Intake,
    validated_name: str,
    say: Callable[[str, str | None], str],
    existing_thread_ts: str = "",
) -> str:
    """Announce a listing so everything said about it lands in one thread.

    A run that never has to ask anything used to reach delivery owning no
    thread, and its finished link posted at channel level naming no property.
    Announcing once every prerequisite has been resolved — but before the build
    — means the thread exists whether the run ends in a question or a link.

    Args:
        connection: An open database connection, for the roster lookup.
        run_id: The run being announced, so its thread root is recorded.
        intake: The submission, for the announcement's wording.
        validated_name: The name the contact check proved.
        say: Posts a message and returns the thread timestamp it landed in.
        existing_thread_ts: The run's current root, if it already owns one.

    Returns:
        The run's thread root: the existing one when it already had it, the new
        one it just opened, or an empty string when the announcement was empty
        or Slack could not be reached.

    Raises:
        Nothing. Slack being unavailable must not turn a verified flyer into a
        failed run; without a thread the outcome still posts, in the channel,
        which is the behaviour that existed before this.
    """
    if existing_thread_ts:
        return existing_thread_ts
    opening = announce(connection, intake, validated_name)
    if not opening:
        return ""
    try:
        thread_ts = say(safe(opening), None)
    except Exception:
        logger.exception("could not announce the listing for run %s", run_id)
        return ""
    if not thread_ts:
        return ""
    current = store.run_by_id(connection, run_id)
    if current is not None:
        store.set_status(
            connection,
            run_id,
            current.status,
            "announced the listing and opened its thread",
            slack_thread_ts=thread_ts,
        )
    return thread_ts
