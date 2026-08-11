"""Joining every piece into one run, from a sheet row to a link in Slack.

`orchestrator.py` decides; this performs. It is the only module that both talks
to the outside world and knows the order of the steps, which keeps every other
module testable in isolation and puts all the I/O in one reviewable place.

The sequence Chase specified:

    identify the agent -> work out the request type -> gather the columns
    -> research what is public -> ask what is not -> build -> check twice
    -> post the link

Two properties worth stating, because they are what make it safe to run
unattended:

* **Every exit is recorded.** A run reaches a status in the database whatever
  happens, including the failure paths, so `AGENTS.md` §6's rule — that a
  listing's state be explainable from the log — holds even when things break.
* **It never guesses.** Anything contradictory or genuinely unknowable stops the
  run at `needs_info` with a question posted, rather than being filled with
  something plausible.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from sqlite3 import Connection
from typing import Any, Final

from gable.db import store
from gable.listings.enrich import Facts, look_up
from gable.listings.intake import Intake
from gable.pipeline.orchestrator import Outcome, after_research, agent_slots, judge, plan
from gable.sheets import repository as repo
from gable.slides import fields as template_fields
from gable.slides.catalog import for_category
from gable.voice import safe

logger = logging.getLogger("gable.runner")

#: Chase asked for two inspections: one to catch the obvious, a second to catch
#: what fixing the first moved.
QUALITY_PASSES: Final[int] = 2


@dataclass
class RunResult:
    """What one run did, in the words Gable would use."""

    run_id: str
    status: str
    said: list[str] = field(default_factory=list)
    output_url: str = ""
    questions: list[str] = field(default_factory=list)

    @property
    def needs_a_human(self) -> bool:
        """True when the run stopped to ask something."""
        return self.status in {"needs_info", "needs_photo", "needs_template"}


@dataclass
class Runner:
    """Performs a run. Every outside call is injected, so this is testable."""

    connection: Connection
    #: Posts a message and returns the thread timestamp it landed in.
    say: Callable[[str, str | None], str]
    #: Finds a template file id for a category. Returns "" when none fits.
    pick_template: Callable[[str], tuple[str, str]]
    #: Reads every text string from a presentation.
    read_slide_text: Callable[[str], list[str]]
    #: Copies a template and returns (file_id, url).
    copy_template: Callable[[str, str], tuple[str, str]]
    #: Applies find/replace pairs to a presentation.
    fill: Callable[[str, dict[str, str]], int]
    #: Looks up public facts for an address.
    research: Callable[[str], Facts] = lambda _address: Facts()

    def run(self, submission: repo.Submission) -> RunResult:
        """Take one submission as far as it can go.

        Args:
            submission: A row that has not been handled.

        Returns:
            What happened, including anything Gable said.

        Raises:
            Nothing. A run that cannot finish records why and stops; raising
            here would leave the database disagreeing with reality.
        """
        run = store.start_run(self.connection, submission.response_row_id)
        result = RunResult(run_id=run.run_id, status="pending")
        intake = submission.intake

        try:
            return self._sequence(run.run_id, intake, result)
        except Exception:
            logger.exception("run %s failed", run.run_id)
            store.set_status(
                self.connection, run.run_id, "failed", "unhandled error during the run"
            )
            result.status = "failed"
            result.said.append(
                safe(
                    "Something went wrong while I was building this one. "
                    "I have stopped rather than posting something half-finished."
                )
            )
            return result

    def _sequence(self, run_id: str, intake: Intake, result: RunResult) -> RunResult:
        """The ordered steps. Split out so `run` owns only the failure boundary."""
        known = store.recall_facts(self.connection, intake.address)

        # 1-3. Identify, route, and see what the columns give us.
        step = plan(intake, known)

        # 4. Research anything public that is missing.
        if step.outcome is Outcome.RESEARCH:
            found = self.research(intake.address)
            if not found.is_empty:
                store.remember_facts(
                    self.connection,
                    intake.address,
                    found.as_dict(),
                    found.source_url,
                    found.confidence,
                )
                known = store.recall_facts(self.connection, intake.address)
            step = after_research(intake, found, known)

        # 5. Anything contradictory or unknowable stops here with a question.
        if step.outcome is Outcome.ASK:
            return self._ask(run_id, step.say, step.questions, result)
        if step.outcome is Outcome.SKIP:
            store.set_status(self.connection, run_id, "skipped", step.detail)
            result.status = "skipped"
            if step.say:
                result.said.append(safe(step.say))
            return result

        # Two agents named with unclear roles is also a question.
        slots = agent_slots(intake)
        if slots.outcome is Outcome.ASK:
            return self._ask(run_id, slots.say, slots.questions, result)

        # 6. Build.
        template_id, template_label = self.pick_template(step.category)
        if not template_id:
            return self._ask(
                run_id,
                f"I do not have a {step.category} design filed yet. Which should I use?",
                [],
                result,
                status="needs_template",
            )
        store.set_status(
            self.connection,
            run_id,
            "building",
            f"using {template_label}",
            template_file_id=template_id,
            template_label=template_label,
        )
        if step.say:
            result.said.append(safe(step.say))

        resolution = template_fields.resolve(self.read_slide_text(template_id))
        values = self._values(intake, known)
        pairs = template_fields.replacements(resolution, values)

        output_id, output_url = self.copy_template(template_id, self._name(intake))
        changed = self.fill(output_id, pairs)
        logger.info("run %s filled %d field(s)", run_id, changed)

        # 7. Check it twice — but only against what this template actually has
        # a slot for. Judging against every value would fail any design without
        # an email field because the email is not on it, which is not a defect.
        placed = {
            name: values[name]
            for name, literal in resolution.fields.items()
            if literal in pairs and values.get(name, "").strip()
        }
        for attempt in range(1, QUALITY_PASSES + 1):
            verdict = judge("\n".join(self.read_slide_text(output_id)), placed, attempt)
            if verdict.passed:
                continue
            if attempt == QUALITY_PASSES:
                store.set_status(
                    self.connection,
                    run_id,
                    "needs_review",
                    "; ".join(verdict.problems),
                    output_file_id=output_id,
                    output_url=output_url,
                )
                result.status = "needs_review"
                result.output_url = output_url
                result.said.append(safe(verdict.say))
                self.say(result.said[-1], None)
                return result

        # 8. Deliver.
        store.set_status(
            self.connection,
            run_id,
            "delivered",
            "posted the link",
            output_file_id=output_id,
            output_url=output_url,
        )
        result.status = "delivered"
        result.output_url = output_url
        message = safe(f"Your flyer is ready. <{output_url}|Open the flyer>")
        result.said.append(message)
        thread = self.say(message, None)
        if thread:
            store.set_status(
                self.connection,
                run_id,
                "delivered",
                "thread recorded",
                slack_thread_ts=thread,
            )
        return result

    def _ask(
        self,
        run_id: str,
        question: str,
        questions: list[Any],
        result: RunResult,
        status: str = "needs_info",
    ) -> RunResult:
        """Stop the run and put a question in Slack."""
        asked = safe(question or "I need one more thing before I can build this.")
        thread = self.say(asked, None)
        store.set_status(self.connection, run_id, status, asked[:200], slack_thread_ts=thread or "")
        result.status = status
        result.said.append(asked)
        result.questions = [asked, *[q.ask for q in questions[1:]]]
        return result

    def _values(self, intake: Intake, known: dict[str, str]) -> dict[str, str]:
        """What should end up on the flyer, from the form plus what was found."""
        person = repo.find_salesperson(self.connection, intake.agent_email)
        name = " ".join(
            part for part in (person.get("first_name", ""), person.get("last_name", "")) if part
        )
        return {
            "address": intake.address,
            "price": intake.price or known.get("list_price", ""),
            "beds": known.get("beds", ""),
            "baths": known.get("baths", ""),
            "square_feet": known.get("square_feet", ""),
            "agent_name": name or intake.agent_name,
            "agent_phone": person.get("phone", ""),
            "agent_email": intake.agent_email,
            "open_house": intake.open_house,
        }

    def _name(self, intake: Intake) -> str:
        """What the finished file is called in Drive, so Carmen can scan for it."""
        return f"{intake.category} — {intake.address} — {intake.agent_name}".strip(" —")


def default_research(api_key: str) -> Callable[[str], Facts]:
    """A research function bound to a Firecrawl key.

    Args:
        api_key: The Firecrawl key. Empty disables lookups, and the run then
            asks rather than researching.

    Returns:
        A callable taking an address.

    Raises:
        Nothing.
    """

    def research(address: str) -> Facts:
        return look_up(address, api_key)

    return research


def template_picker(
    list_templates: Callable[[], list[dict[str, str]]],
) -> Callable[[str], tuple[str, str]]:
    """Choose a template file for a category.

    Args:
        list_templates: Returns Drive files with `id`, `name` and a
            `gable_category`.

    Returns:
        A callable mapping a category to `(file_id, label)`, empty when none
        fits — which becomes a question rather than a guess.

    Raises:
        Nothing.
    """

    def pick(category: str) -> tuple[str, str]:
        if not category or not for_category(category):
            return "", ""
        wanted = {entry.filename for entry in for_category(category)}
        for candidate in list_templates():
            if candidate.get("name") in wanted:
                return candidate["id"], candidate["name"]
        return "", ""

    return pick
