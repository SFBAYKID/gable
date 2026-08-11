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
import re
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

#: A phone number in any of the shapes these templates use: 443.499.3839,
#: (443) 555-0142, 410-305-9006.
_PHONE_ON_FLYER: Final[re.Pattern[str]] = re.compile(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}")

#: An email address, which on a listing flyer is always somebody's real one.
_EMAIL_ON_FLYER: Final[re.Pattern[str]] = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")

#: Every Corner House agent is on this domain, so a roster row missing its own
#: URL still produces a real website rather than the word "Website".
DEFAULT_BROKERAGE_URL: Final[str] = "cornerhouserealty.com"

#: The brokerage's main line, used when the roster has no direct number for an
#: agent. Chase's rule: a missing agent number falls back to the main number on
#: the site rather than stopping the run. VERIFIED 2026-08-11 by reading
#: cornerhouserealty.com, where it is the most frequently listed number and
#: appears on the templates themselves as the office line.
OFFICE_PHONE: Final[str] = "443.499.3839"

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
    #: Root Slack thread for a resumed run. Replies return their own timestamp,
    #: but the run must keep this root so the next human response still maps.
    origin_thread_ts: str = ""
    #: Places the hero photo into a rendered flyer.
    place_photo: Callable[[str, str, str], bool] = lambda _fid, _url, _template: False
    #: Puts the agent's own face where the sample face was. Returns False when
    #: the design has no recognisable headshot frame, which is a flyer worth a
    #: look rather than a failure.
    place_headshot: Callable[[str, str], bool] = lambda _fid, _url: False
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

        # Read the flyer back and require every value to appear exactly as it
        # was supplied. A delivered flyer once carried "$460,0000" — four zeros
        # — against a submission that supplied "$685,000", and every check
        # passed, because the vision pass reads layout and a plausible-looking
        # wrong number is not a layout problem. Counting replacements is not
        # enough either: `replaceAllText` reported success while corrupting the
        # text it matched inside.
        #
        # A wrong price on a real address is the worst thing this system can
        # produce, so this is deterministic rather than a judgement.
        stray: list[str] = []
        wrong = self._values_not_readable_back(output_id, values, pairs)
        if not wrong:
            # A phone number or email on the flyer that this run did not supply
            # belongs to the template's sample agent. A delivered flyer carried
            # "Stacey Abbott, 410.952.6193, sabbotthomes@gmail.com" from a
            # two-agent design's second slot and passed every check, because the
            # readback above can only verify that supplied values appear — it
            # has nothing to compare against for a value never supplied.
            #
            # Someone else's phone number and personal email on a client-facing
            # flyer is worse than any layout defect, so this is an absence check
            # and it is deterministic.
            stray = self._foreign_contact_details(output_id, values)
        if wrong or stray:
            store.set_status(
                self.connection,
                run_id,
                "needs_review",
                (
                    f"a filled value did not read back correctly: {wrong[0]}"
                    if wrong
                    else f"contact details that are not this listing's: {stray[0]}"
                )[:400],
                output_file_id=output_id,
                output_url=output_url,
            )
            result.status = "needs_review"
            result.output_url = output_url
            # Two different problems, two different sentences. Reading Gable's
            # own words in Slack caught these being spliced together into "the
            # phone number 410.456.6868 is not this listing's on it does not
            # match what I was given" — one message built from a template that
            # expects a bare field name and a value that is already a sentence.
            spoken = safe(
                f"I filled the design but the {wrong[0]} on it does not match what I was "
                "given, so I have not sent it as finished."
                if wrong
                else f"I built the flyer but the {stray[0]}, so I have not sent it as finished."
            )
            result.said.append(spoken)
            self.say(spoken, None)
            return result

        placed = self.place_photo(output_id, self.hero_photo_url, template_label)
        # The sample face is the most visible thing Gable gets wrong: one agent's
        # name beside another agent's photograph. Best effort — a design with no
        # headshot frame is still a deliverable flyer.
        if placed and values.get("headshot"):
            if self.place_headshot(output_id, values["headshot"]):
                logger.info("replaced the sample headshot for run %s", run_id)
            else:
                logger.info("kept the design's own headshot for run %s", run_id)
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
            posted_ts = self.say(spoken, self.origin_thread_ts or None)
            self.say(
                safe(f"Have a look and tell me what to change. <{output_url}|Open it>"),
                self.origin_thread_ts or posted_ts,
            )
            self._remember_thread(run_id, posted_ts)
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
        posted_ts = self.say(message, self.origin_thread_ts or None)
        thread_root = self.origin_thread_ts or posted_ts
        if thread_root:
            store.set_status(
                self.connection,
                run_id,
                "delivered",
                "thread recorded",
                slack_thread_ts=thread_root,
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
        posted_ts = self.say(asked, self.origin_thread_ts or None)
        thread_root = self.origin_thread_ts or posted_ts
        store.set_status(
            self.connection,
            run_id,
            status,
            asked[:200],
            slack_thread_ts=thread_root,
        )
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
            "agent_phone": person.get("phone", "") or OFFICE_PHONE,
            "agent_email": intake.agent_email,
            "open_house": intake.open_house,
            # `fields.PATTERNS` recognises a website slot and this dictionary
            # did not supply one, so `replacements()` skipped it and the literal
            # word "Website" survived onto the flyer — which then failed its own
            # quality check. The roster already carries the URL.
            "website": person.get("brokerage_url", "") or DEFAULT_BROKERAGE_URL,
            # Not a text replacement — `place_headshot` consumes this. Every
            # agent's photo lives on cornerhouserealty.com and the roster
            # mirrors it; an empty value leaves the design's own face alone.
            "headshot": person.get("headshot_url", ""),
        }

    def _values_not_readable_back(
        self, file_id: str, values: dict[str, str], pairs: dict[str, str]
    ) -> list[str]:
        """Which supplied values do not appear verbatim on the rendered flyer.

        Args:
            file_id: The filled presentation.
            values: What the run intended to put on the flyer.
            pairs: The literal-to-value replacements actually sent.

        Returns:
            Field names whose value is missing or corrupted, worst first. Empty
            when every value reads back exactly.

        Raises:
            Nothing. A read failure returns no complaints rather than blocking a
            flyer on a transient Slides error; the other guards still apply.
        """
        try:
            text = "\n".join(self.read_slide_text(file_id))
        except Exception:
            logger.exception("could not read the flyer back for verification")
            return []
        if not text:
            return []

        # Only values actually sent to the design. A value the template has no
        # slot for is not expected to appear.
        sent = {value for value in pairs.values() if value.strip()}
        missing: list[str] = []
        for name, value in values.items():
            candidate = value.strip()
            if not candidate or candidate not in sent:
                continue
            # `headshot` is an image URL, never text on the flyer.
            if name == "headshot":
                continue
            if candidate not in text:
                missing.append(name.replace("_", " "))
        return missing

    def _foreign_contact_details(self, file_id: str, values: dict[str, str]) -> list[str]:
        """Contact details on the flyer that this run did not put there.

        Args:
            file_id: The filled presentation.
            values: What the run supplied.

        Returns:
            A description of each stray phone number or email, worst first.
            Empty when every one on the flyer came from this submission.

        Raises:
            Nothing. A read failure returns no complaints rather than blocking
            on a transient Slides error.
        """
        try:
            text = "\n".join(self.read_slide_text(file_id))
        except Exception:
            logger.exception("could not read the flyer back for a contact check")
            return []
        if not text:
            return []

        def digits(value: str) -> str:
            return "".join(c for c in value if c.isdigit())

        supplied_phones = {digits(v) for v in values.values() if digits(v)}
        supplied_emails = {v.strip().lower() for v in values.values() if "@" in v}

        stray: list[str] = []
        for found in _PHONE_ON_FLYER.findall(text):
            if digits(found) and digits(found) not in supplied_phones:
                stray.append(f"phone number {found} is not this listing's")
        for found in _EMAIL_ON_FLYER.findall(text):
            if found.strip().lower() not in supplied_emails:
                stray.append(f"email address {found} is not this listing's")
        return stray

    def _remember_thread(self, run_id: str, thread_ts: str) -> None:
        """Record which Slack thread a run is being discussed in.

        Without this Carmen cannot edit the flyer. `run_for_thread` maps her
        reply back to the run, and only the delivered path was recording the
        thread — so every flyer that stopped for review, which is precisely the
        one she would want to change, answered "I could not match this thread to
        a listing".

        Args:
            run_id: The run to attach the thread to.
            thread_ts: The Slack timestamp the conversation is rooted at.

        Raises:
            Nothing.
        """
        root = self.origin_thread_ts or thread_ts
        if not root:
            return
        # Re-assert the status the run already has. `set_status` writes whatever
        # it is given, so passing an empty string here would blank the run's
        # state while recording the thread — losing the very thing that tells
        # the poller not to build this listing again.
        store.set_status(
            self.connection,
            run_id,
            self._status_of(run_id),
            "thread recorded",
            slack_thread_ts=root,
        )

    def _status_of(self, run_id: str) -> str:
        """The status a run currently holds.

        Args:
            run_id: Which run.

        Returns:
            Its status, or `needs_review` if it cannot be read — the
            conservative answer, since it keeps the run out of the poller.

        Raises:
            Nothing.
        """
        try:
            row = self.connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        except Exception:
            return "needs_review"
        return str(row["status"]) if row else "needs_review"

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
