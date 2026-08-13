"""One listing, from a sheet row to a link in Slack.

The flow Chase specified, in order:

1. read the row and identify the agent
2. work out the request type, and from it the template category
3. gather columns L through T
4. research anything public that is missing — beds, baths, square footage, price
5. ask about anything contradictory, and about the hero photo
6. build the flyer
7. check it twice
8. post the link

**What this module does and does not do.** It decides; it does not perform. Every
step returns a `Step` describing what happened and what should happen next, and
the caller does the I/O. That is what makes the whole sequence testable without
Google, Slack or a paid call — and what keeps a wrong decision from becoming a
wrong action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from gable.listings.enrich import Facts, fill_gaps
from gable.listings.intake import (
    Intake,
    Question,
    address_looks_usable,
    incoherences,
    mentions_multiple_agents,
    missing_public_facts,
    named_agents,
    needs_two_agents,
)


class Outcome(StrEnum):
    """What should happen next with a listing."""

    RESEARCH = "research"
    ASK = "ask"
    BUILD = "build"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class Step:
    """One decision, and why."""

    outcome: Outcome
    #: What Gable says, if this step involves speaking. Already plain English.
    say: str = ""
    #: Facts still to look up.
    research: list[str] = field(default_factory=list)
    #: Questions for Carmen, in the order they should be asked.
    questions: list[Question] = field(default_factory=list)
    #: Template category, once known.
    category: str = ""
    #: Anything worth recording against the run.
    detail: str = ""


def plan(intake: Intake, known_facts: dict[str, str] | None = None) -> Step:
    """Decide what to do with a submission before any work is done.

    Args:
        intake: The parsed row.
        known_facts: Facts already cached for this address.

    Returns:
        The next `Step`. `ASK` takes precedence over `RESEARCH`, because a
        contradiction makes research pointless — there is no sense looking up a
        property whose address is a Google review link.

    Raises:
        Nothing.
    """
    problems = incoherences(intake)
    if problems:
        return Step(
            outcome=Outcome.ASK,
            questions=problems,
            say=problems[0].ask,
            category=intake.category,
            detail=f"{len(problems)} thing(s) do not add up",
        )

    # No category gate. A design is whatever file in Generic Templates carries
    # this request type's name, so a request the old catalogue had no category
    # for — "Postcard Order", "Video Editing Request" — builds the moment
    # Carmen files a design under that name. When there is no such file the
    # picker stops the run at `needs_template` and names the file it wanted,
    # which is a more useful sentence than a category nobody outside this code
    # has heard of.

    outstanding = missing_public_facts(intake, known_facts)
    if outstanding and address_looks_usable(intake.address):
        return Step(
            outcome=Outcome.RESEARCH,
            research=outstanding,
            category=intake.category,
            detail=f"looking up {', '.join(outstanding)}",
        )

    return Step(
        outcome=Outcome.BUILD,
        category=intake.category,
        detail="everything needed is in hand",
    )


def after_research(intake: Intake, found: Facts, known: dict[str, str]) -> Step:
    """Decide what to do once a lookup has come back.

    Args:
        intake: The parsed row.
        found: What research turned up.
        known: What was already known.

    Returns:
        `BUILD` when enough is in hand, or `ASK` when research could not settle
        something a flyer genuinely needs.

    Raises:
        Nothing.
    """
    merged, looked_up = fill_gaps(known, found)
    still_missing = [name for name in ("beds", "baths", "square_feet") if not merged.get(name)]

    if found.caveats:
        # A number that looks wrong is worse than a blank, so it gets a question
        # rather than being quietly used.
        return Step(
            outcome=Outcome.ASK,
            questions=[Question("researched facts", f"{found.caveats[0]}. Can you confirm it?")],
            say=f"{found.caveats[0].capitalize()}. Can you confirm it before I build this?",
            category=intake.category,
            detail="research returned an implausible value",
        )

    if still_missing and not intake.price:
        readable = ", ".join(name.replace("_", " ") for name in still_missing)
        return Step(
            outcome=Outcome.ASK,
            questions=[Question("facts", f"I could not find the {readable}. Do you have them?")],
            say=f"I could not find the {readable} for this one. Do you have them?",
            category=intake.category,
            detail=f"research did not settle {readable}",
        )

    said = ""
    if looked_up:
        said = f"I looked up the {', '.join(looked_up)} from the address."
    return Step(
        outcome=Outcome.BUILD,
        say=said,
        category=intake.category,
        detail=f"researched {', '.join(looked_up)}" if looked_up else "nothing new needed",
    )


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    """What an inspection pass concluded about a rendered flyer."""

    passed: bool
    pass_number: int
    problems: list[str] = field(default_factory=list)

    @property
    def say(self) -> str:
        """What Gable tells Carmen about this pass."""
        if self.passed:
            return ""
        first = self.problems[0] if self.problems else "something looks off"
        return f"I rendered it, but {first}"


def judge(rendered_text: str, expected_values: dict[str, str], pass_number: int) -> QualityVerdict:
    """Inspect a rendered flyer for the failures that are silent.

    Every API call can succeed while the result is wrong: a placeholder left
    visible, a value that never landed, a token that survived. Those are what
    this checks.

    **It cannot see the layout, and that is a real limit rather than a
    quibble.** Observed on a delivered flyer: the price read "$510,000" in the
    document and rendered clipped to "$510,00" because the box was sized for a
    shorter placeholder; the address overflowed onto the divider and the email
    ran off the bottom of the slide. Every value was present, so this passed it.
    Catching that needs the connected, fail-closed vision pass over the rendered
    thumbnail (ARCHITECTURE.md §4.7b). This function remains the deterministic
    text half of that two-layer gate.

    Args:
        rendered_text: All text read back from the rendered slide.
        expected_values: What should appear, by field name.
        pass_number: Which inspection this is, 1 or 2.

    Returns:
        A verdict. Chase asked for two passes; the second runs after any repair
        the first prompted, and catches what the repair moved.

    Raises:
        Nothing.
    """
    problems: list[str] = []

    leftovers = [
        fragment
        for fragment in ("[", "{{", "PROPERTY ADDRESS", "AGENT NAME", "[PRICE]")
        if fragment in rendered_text
    ]
    if leftovers:
        problems.append("some of the template's own placeholder text is still showing")

    for name, value in expected_values.items():
        if value and value not in rendered_text:
            readable = name.replace("_", " ")
            problems.append(f"the {readable} did not make it onto the flyer")

    return QualityVerdict(passed=not problems, pass_number=pass_number, problems=problems)


def agent_slots(intake: Intake) -> Step:
    """Work out whether this listing needs a one- or two-agent design.

    Args:
        intake: The parsed row.

    Returns:
        A `BUILD` step naming the design shape, or `ASK` when two agents are
        named but their roles are ambiguous.

    Raises:
        Nothing.
    """
    roles = named_agents(intake)
    if not needs_two_agents(intake):
        if mentions_multiple_agents(intake):
            return Step(
                outcome=Outcome.ASK,
                questions=[
                    Question(
                        "agents",
                        "This sounds like a two-agent post. Who are the two agents, "
                        "and which role does each have?",
                    )
                ],
                say=(
                    "The notes make this sound like a two-agent post, but they do not "
                    "name both roles clearly. Who are the two agents, and which one is "
                    "listing or hosting?"
                ),
                category=intake.category,
                detail="multiple agents implied, roles unclear",
            )
        return Step(outcome=Outcome.BUILD, detail="one agent", category=intake.category)

    listing_agent = roles.get("listing", "")
    hosting_agent = roles.get("hosting", "")
    if listing_agent and hosting_agent:
        return Step(
            outcome=Outcome.BUILD,
            category=intake.category,
            detail=f"two agents: {listing_agent} listing, {hosting_agent} hosting",
        )
    return Step(
        outcome=Outcome.ASK,
        questions=[Question("agents", "Which of them is the listing agent?")],
        say=(
            f"This one names {' and '.join(sorted(set(roles.values())))}. "
            "Which of them is the listing agent?"
        ),
        category=intake.category,
        detail="two agents named, roles unclear",
    )
