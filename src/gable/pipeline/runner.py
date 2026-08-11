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

from gable import spend
from gable.db import store
from gable.listings.enrich import Facts, look_up
from gable.listings.intake import Intake
from gable.pipeline.orchestrator import Outcome, after_research, agent_slots, judge, plan
from gable.pipeline.vision import Inspection, inspect
from gable.sheets import repository as repo
from gable.slides import fields as template_fields
from gable.slides import fitting
from gable.slides import manifest as template_manifest
from gable.slides.catalog import for_category
from gable.slides.selection import rank as rank_templates
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
        return self.status in {"needs_info", "needs_photo", "needs_template", "needs_review"}


@dataclass
class Runner:
    """Performs a run. Every outside call is injected, so this is testable."""

    connection: Connection
    #: Posts a message and returns the thread timestamp it landed in.
    say: Callable[[str, str | None], str]
    #: Finds a template file for a category and this listing. Returns "" when
    #: none fits, which becomes a question rather than a guess.
    pick_template: Callable[[str, Intake], tuple[str, str]]
    #: Reads every text string from a presentation.
    read_slide_text: Callable[[str], list[str]]
    #: Copies a template and returns (file_id, url).
    copy_template: Callable[[str, str], tuple[str, str]]
    #: Applies find/replace pairs to a presentation.
    fill: Callable[[str, dict[str, str]], int]
    #: Reads every text box with its size and geometry, for fitting.
    read_text_boxes: Callable[[str], list[fitting.TextBox]] = lambda _fid: []
    #: Applies Slides requests to a presentation.
    apply: Callable[[str, list[dict[str, Any]]], None] = lambda _fid, _reqs: None
    #: Renders a slide to PNG bytes, for the vision check.
    thumbnail: Callable[[str], bytes] = lambda _fid: b""
    #: Looks at a rendered flyer. Injected like every other outside call, so a
    #: test can supply a verdict without spending a vision call.
    look_at: Callable[[bytes], Inspection] = inspect
    #: The hero photo for this listing, already fitted and published, or "" if
    #: none has been supplied yet.
    hero_photo_url: str = ""
    #: Places the hero photo into a rendered flyer.
    place_photo: Callable[[str, str, str], bool] = lambda _fid, _url, _template: False
    #: Proves that the photo URL is usable for the target slot. The live
    #: builder supplies the network checker; the runner itself performs no
    #: hidden I/O.
    check_photo: Callable[[str, str], tuple[bool, str]] = lambda _url, _slot: (True, "")
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
        try:
            run = store.start_run(self.connection, submission.response_row_id)
        except store.RunLimitReachedError:
            latest = store.latest_run(self.connection, submission.response_row_id)
            result = RunResult(
                run_id=latest.run_id if latest else "",
                status="failed",
            )
            spoken = safe(
                "I have already tried this listing three times, so I stopped before "
                "starting it again. It needs a person to check what keeps failing."
            )
            result.said.append(spoken)
            self.say(spoken, latest.slack_thread_ts if latest else None)
            return result
        result = RunResult(run_id=run.run_id, status="pending")
        return self._perform(run.run_id, submission.intake, result)

    def resume(self, submission: repo.Submission, run_id: str) -> RunResult:
        """Continue an existing paused run after Carmen supplies its photo.

        Args:
            submission: The locally stored intake row for the paused run.
            run_id: The existing run to continue; no new attempt is opened.

        Returns:
            What the resumed run did.

        Raises:
            Nothing. The same failure boundary as a new run records problems.
        """
        store.set_status(self.connection, run_id, "pending", "resumed from its Slack thread")
        result = RunResult(run_id=run_id, status="pending")
        return self._perform(run_id, submission.intake, result)

    def _perform(self, run_id: str, intake: Intake, result: RunResult) -> RunResult:
        """Run the sequence behind one shared, state-recording failure boundary."""
        try:
            return self._sequence(run_id, intake, result)
        except Exception:
            logger.exception("run %s failed", run_id)
            store.set_status(self.connection, run_id, "failed", "unhandled error during the run")
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

        # 5b. Ask for the hero photo BEFORE building anything.
        #
        # This is Chase's step 4, and skipping it was a real failure: a flyer
        # was delivered showing the template's own sky-and-grass placeholder
        # where the house should be, and announced as ready. A listing flyer
        # without a photograph of the listing is not a draft, it is wrong, so
        # the run stops here rather than producing one.
        if not self.hero_photo_url:
            return self._ask(
                run_id,
                f"I have everything for {intake.address} except the photo. "
                "Which image do you want as the hero?",
                [],
                result,
                status="needs_photo",
            )

        # 6. Build.
        template_id, template_label = self.pick_template(step.category, intake)
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

        # What THIS design needs, not one global column set. Two flyers were
        # reviewed and their field sets differed; treating them as one is what
        # shipped a flyer carrying the literal words "Phone" and "Website".
        manifest = template_manifest.manifest_for(template_label)
        values["address"] = template_manifest.normalise_address(values.get("address", ""))
        values["hero_photo"] = self.hero_photo_url
        field_problems = template_manifest.validate(manifest, values)
        blocking = [item for item in field_problems if item.blocking]
        if blocking:
            return self._ask(run_id, blocking[0].say, [], result)
        for advisory in field_problems:
            result.said.append(safe(advisory.say))
            self.say(result.said[-1], None)

        # An image URL is not checked by ending in .jpg. One flyer put the
        # template's own background illustration in the headshot frame because
        # nothing looked at what was behind the link.
        hero_slot = manifest.find("hero_photo")
        if hero_slot:
            usable, why_not = self.check_photo(self.hero_photo_url, hero_slot.aspect or "any")
            if not usable:
                return self._ask(
                    run_id,
                    f"I could not use that photo — {why_not}. Could you send another?",
                    [],
                    result,
                    status="needs_photo",
                )

        pairs = template_fields.replacements(resolution, values)

        output_id, output_url = self.copy_template(template_id, self._name(intake))
        changed = self.fill(output_id, pairs)
        logger.info("run %s filled %d field(s)", run_id, changed)
        if not pairs or changed != len(pairs):
            store.set_status(
                self.connection,
                run_id,
                "needs_review",
                "the template text did not match each intended field exactly once",
                output_file_id=output_id,
                output_url=output_url,
            )
            result.status = "needs_review"
            result.output_url = output_url
            spoken = safe(
                "I copied the design, but one of its fields did not match exactly once. "
                "I stopped before changing any text."
            )
            result.said.append(spoken)
            self.say(spoken, None)
            return result

        placed = self.place_photo(output_id, self.hero_photo_url, template_label)
        if not placed:
            # The photo is the point of the flyer. Delivering without it, after
            # being given one, is worse than stopping.
            store.set_status(
                self.connection,
                run_id,
                "needs_review",
                "the hero photo could not be placed",
                output_file_id=output_id,
                output_url=output_url,
            )
            result.status = "needs_review"
            result.output_url = output_url
            spoken = safe(
                "I built the flyer but could not get the photo onto it. "
                "I have not sent it as finished."
            )
            result.said.append(spoken)
            self.say(spoken, None)
            return result

        # 7a. Fit the text to its boxes. Slides cannot autofit over the API —
        # verified: "Autofit types other than NONE are not supported" — so a
        # value longer than its placeholder clips silently. This is what shipped
        # a price reading $510,000 as $510,00.
        fits = fitting.plan_fits(self.read_text_boxes(output_id))
        shrunk = [fit for fit in fits if fit.overflows]
        if shrunk:
            self.apply(output_id, fitting.requests_for(fits))
            logger.info("run %s refitted %d text box(es)", run_id, len(shrunk))
        unreadable = [fit for fit in shrunk if fit.too_small_to_read]

        # 7b. Check it twice: once on the text, once by looking at it. The text
        # pass verifies every value is PRESENT; only the vision pass can see
        # whether it FITS, and that gap delivered a clipped flyer once already.
        expected = {
            name: values[name]
            for name, literal in resolution.fields.items()
            if literal in pairs and values.get(name, "").strip()
        }
        verdict = judge("\n".join(self.read_slide_text(output_id)), expected, 1)
        seen: Inspection = self.look_at(self.thumbnail(output_id))

        problems = list(verdict.problems)
        if seen.checked and not seen.looks_right:
            problems.extend(seen.problems)
        if unreadable:
            problems.append(
                f"the {unreadable[0].text[:24]} had to be shrunk so far it is hard to read"
            )

        if problems:
            store.set_status(
                self.connection,
                run_id,
                "needs_review",
                "; ".join(problems)[:400],
                output_file_id=output_id,
                output_url=output_url,
            )
            result.status = "needs_review"
            result.output_url = output_url
            spoken = seen.say or verdict.say or safe(f"I rendered it, but {problems[0]}")
            result.said.append(spoken)
            self.say(spoken, None)
            self.say(safe(f"Have a look and tell me what to change. <{output_url}|Open it>"), None)
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


def default_research(
    api_key: str,
    connection: Connection | None = None,
) -> Callable[[str], Facts]:
    """A research function bound to a Firecrawl key.

    Args:
        api_key: The Firecrawl key. Empty disables lookups, and the run then
            asks rather than researching.
        connection: Spend ledger for the live paid path. Tests and offline
            callers may omit it.

    Returns:
        A callable taking an address.

    Raises:
        Nothing.
    """

    def research(address: str) -> Facts:
        if connection is None or not api_key:
            return look_up(address, api_key)
        estimate = spend.Estimate(
            service="firecrawl",
            model="search",
            usd=spend.FIRECRAWL_PER_SEARCH,
            detail="one property search reservation",
        )
        try:
            return spend.guarded_call(connection, estimate, lambda: look_up(address, api_key))
        except spend.BudgetExceededError:
            return Facts(caveats=["Testing has reached its spending limit"])

    return research


def template_picker(
    list_templates: Callable[[], list[dict[str, str]]],
) -> Callable[[str, Intake], tuple[str, str]]:
    """Choose a template for a category AND this particular listing.

    Picking the first match in Drive order put a plain new listing onto a
    "Just Listed plus Open House" design: the open-house tag stayed, its date
    fields had nothing to fill them, and the headline overlapped the empty
    date. Correct category, wrong design.

    So the choice is scored. A design that needs a fact this listing does not
    have is penalised, and one whose name says it is the clean variant is
    preferred.

    Args:
        list_templates: Returns Drive files with `id` and `name`.

    Returns:
        A callable taking `(category, intake)` and returning `(file_id, label)`,
        empty when nothing fits.

    Raises:
        Nothing.
    """

    def pick(category: str, intake: Intake) -> tuple[str, str]:
        if not category or not for_category(category):
            return "", ""
        available = {str(item.get("name") or ""): item for item in list_templates()}
        ranked = rank_templates(category, intake)
        if not ranked:
            return "", ""
        candidate = available.get(ranked[0].filename)
        if candidate is None:
            return "", ""
        return str(candidate["id"]), str(candidate["name"])

    return pick
