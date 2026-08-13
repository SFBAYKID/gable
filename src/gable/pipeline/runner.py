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
from typing import Any

from gable.agents import website as agent_website
from gable.db import store
from gable.listings.enrich import Facts
from gable.listings.intake import Intake, needs_two_agents, price_note
from gable.pipeline import audit, people, research_gate, run_reporting, run_values
from gable.pipeline.contact_gate import ContactGate
from gable.pipeline.orchestrator import Outcome, agent_slots, judge, plan
from gable.pipeline.vision import Inspection, inspect
from gable.sheets import repository as repo
from gable.slides import fields as template_fields
from gable.slides import fitting, preflight
from gable.slides import manifest as template_manifest
from gable.voice import safe

logger = logging.getLogger("gable.runner")


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
    #: Refuses a source whose proactive template audit is still unresolved.
    #: Empty means the source is cleared for listing-specific preflight.
    template_clearance: Callable[[str, str], str] = lambda _fid, _label: ""
    #: Reads every text box with its size and geometry, for fitting.
    read_text_boxes: Callable[[str], list[fitting.TextBox]] = lambda _fid: []
    #: Measures the current source template and this listing's real values
    #: before any Drive copy is created.
    preflight_template: Callable[
        [str, str, str, template_fields.Resolution, dict[str, str]], preflight.Report
    ] = lambda _fid, _label, _category, _resolution, _values: preflight.Report()
    #: Applies Slides requests to a presentation.
    apply: Callable[[str, list[dict[str, Any]]], None] = lambda _fid, _reqs: None
    #: Renders a slide to PNG bytes, for the vision check.
    thumbnail: Callable[[str], bytes] = lambda _fid: b""
    #: Looks at a rendered flyer. Injected like every other outside call, so a
    #: test can supply a verdict without spending a vision call.
    look_at: Callable[[str, bytes], Inspection] = lambda _run_id, image: inspect(image)
    #: The hero photo for this listing, already fitted and published, or "" if
    #: none has been supplied yet.
    hero_photo_url: str = ""
    #: Root Slack thread for a resumed run. Replies return their own timestamp,
    #: but the run must keep this root so the next human response still maps.
    origin_thread_ts: str = ""
    #: Places the hero photo into a rendered flyer.
    place_photo: Callable[[str, str, str, str], bool] = lambda _run, _fid, _url, _template: False
    #: Puts the agent's own face where the sample face was. Returns False when
    #: the design has no recognisable headshot frame, which is a flyer worth a
    #: look rather than a failure.
    place_headshot: Callable[[str, str], bool | None] = lambda _fid, _url: None
    #: Proves that the photo URL is usable for the target slot. The live
    #: builder supplies the network checker; the runner itself performs no
    #: hidden I/O.
    check_photo: Callable[[str, str], tuple[bool, str]] = lambda _url, _slot: (True, "")
    #: Looks up public facts for an address.
    research: Callable[[str, frozenset[str]], Facts] = lambda _address, _fields: Facts()
    #: Checks the official brokerage profile only when the refreshed contact
    #: workbook has no complete, exact record. The unit-test default performs
    #: no hidden I/O; ``pipeline.live`` supplies the bounded website client.
    official_contact_lookup: Callable[[str, str], agent_website.ProfileLookup] = (
        lambda _name, _email: agent_website.ProfileLookup(
            problem=(
                "I could not complete the check against the official Corner House Realty website"
            )
        )
    )
    #: Publishes this agent's headshot and returns a URL Slides can fetch.
    #: Preflight stops if the design has a headshot well and this stays empty.
    headshot_for: Callable[[str], str] = lambda _name: ""
    #: Names the stage being worked on, for Slack's waiting indicator. Cosmetic
    #: by contract: it is called around real work and must never affect it.
    progress: Callable[[str], None] = lambda _stage: None
    #: Legacy warning approvals retained while older paused rows are migrated.
    #: Correctable text and photo fitting no longer waits for an approval.
    approved_template_warning_codes: frozenset[str] = frozenset()

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
        except store.RunAlreadyActiveError:
            latest = store.latest_run(self.connection, submission.response_row_id)
            result = RunResult(
                run_id=latest.run_id if latest else "",
                status=latest.status if latest else "failed",
            )
            spoken = safe(
                "This listing is already being worked on, so I did not start a second "
                "copy of the same work."
            )
            result.said.append(spoken)
            self.say(spoken, latest.slack_thread_ts if latest else None)
            return result
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

    def resume(
        self,
        submission: repo.Submission,
        run_id: str,
        *,
        resume_fields: dict[str, str | int] | None = None,
    ) -> RunResult:
        """Continue an existing paused run after Carmen supplies its photo.

        Args:
            submission: The locally stored intake row for the paused run.
            run_id: The existing run to continue; no new attempt is opened.
            resume_fields: Photo provenance or other run fields that must be
                committed atomically with the paused-run claim.

        Returns:
            What the resumed run did.

        Raises:
            Nothing. The same failure boundary as a new run records problems.
        """
        fields = {"failure_reason": "", **(resume_fields or {})}
        if not store.claim_paused_run(self.connection, run_id, fields):
            current = store.run_by_id(self.connection, run_id)
            result = RunResult(
                run_id=run_id,
                status=current.status if current is not None else "failed",
            )
            spoken = safe(
                "This listing is already being rechecked or is no longer waiting, so I "
                "did not start another copy of the same work."
            )
            result.said.append(spoken)
            self.say(spoken, self.origin_thread_ts or None)
            return result
        result = RunResult(run_id=run_id, status="pending")
        return self._perform(run_id, submission.intake, result)

    def _perform(self, run_id: str, intake: Intake, result: RunResult) -> RunResult:
        """Run the sequence behind one shared, state-recording failure boundary."""
        try:
            return self._sequence(run_id, intake, result)
        except Exception:
            logger.exception("run %s failed", run_id)
            try:
                store.set_status(
                    self.connection,
                    run_id,
                    "failed",
                    "unhandled error during the run",
                    failure_reason="a processing step failed before an outcome was confirmed",
                )
            except Exception:
                # Preserve Runner.run's no-raise boundary even if recording fails.
                logger.exception("could not record failure for run %s", run_id)
            result.status = "failed"
            spoken = safe(
                "I could not finish building this flyer because one of its processing "
                "steps failed. I stopped without sending it as finished."
            )
            try:
                posted_ts = self.say(spoken, self.origin_thread_ts or None)
            except Exception:
                # Slack may be both the original failure and unavailable notice path.
                logger.exception("could not post failure notice for run %s", run_id)
            else:
                if posted_ts:
                    result.said.append(spoken)
                    try:
                        run_reporting.remember_thread(
                            self.connection,
                            run_id,
                            posted_ts,
                            self.origin_thread_ts,
                        )
                    except Exception:
                        logger.exception("could not record the failure thread for run %s", run_id)
            return result

    def _sequence(self, run_id: str, intake: Intake, result: RunResult) -> RunResult:
        """The ordered steps. Split out so `run` owns only the failure boundary."""
        self.progress("is checking the agent contact information...")
        contact_gate = ContactGate(self.connection, intake, self.official_contact_lookup)
        contact = contact_gate.check(run_id)
        if not contact.ready:
            return self._ask(run_id, contact.problem, [], result, status="needs_info")
        # Contradictions are cheap and source-independent. They stop before
        # template reads, web calls, or Slack; property research deliberately
        # waits until the selected source reveals which facts it displays.
        step = plan(intake, required_public_facts=frozenset())
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

        # The parser can prove who is listing and who is hosting, but the
        # generic template contract does not yet identify which text and photo
        # objects belong to each role. Filling repeated labels by page order
        # produced a polished but false flyer in the old partial path. Stop
        # before source selection until a two-agent template has an explicit,
        # testable slot contract.
        if needs_two_agents(intake):
            return self._ask(
                run_id,
                "This request needs two agent placements, but that template layout is not "
                "certified yet. I cannot prove which text and photo spot belongs to the "
                "listing agent and which belongs to the hosting agent, so I have not built it.",
                [],
                result,
                status="needs_template",
            )

        # Select the exact request-type source before either source-specific
        # credentials or public-property research. Its text decides whether a
        # REALTOR title, beds, baths, square footage, or price is needed. Core
        # contact validation already passed above; every remaining prerequisite
        # still precedes preflight, any Slack message, and every Drive copy.
        self.progress("is choosing the design...")
        template_id, template_label = self.pick_template(step.category, intake)
        if not template_id:
            # Name the file it is looking for, not the category it mapped to.
            # A design is added by putting it in Generic Templates and calling
            # it exactly what the form calls this request, so the useful
            # sentence says which name is missing rather than asking Carmen to
            # nominate one.
            wanted = intake.request_type.strip() or "this request type"
            return self._ask(
                run_id,
                f"I do not have a design named {wanted} in the Generic Templates "
                "folder, so I have not built anything. Add one with that exact "
                "name and I will use it.",
                [],
                result,
                status="needs_template",
            )
        store.set_status(
            self.connection,
            run_id,
            "pending",
            f"selected {template_label} for source clearance and preflight",
            template_file_id=template_id,
            template_label=template_label,
        )
        resolution = template_fields.resolve(self.read_slide_text(template_id))
        if "agent_title" in resolution.fields:
            contact = contact_gate.check(run_id, require_title=True)
            if not contact.ready:
                return self._ask(run_id, contact.problem, [], result, status="needs_info")
        clearance_problem = self.template_clearance(template_id, template_label)
        if clearance_problem:
            return self._ask(
                run_id,
                clearance_problem,
                [],
                result,
                status="needs_template",
            )
        step, known = research_gate.resolve(
            self.connection,
            intake,
            resolution,
            self.research,
            lambda: self.progress("is looking up the property..."),
        )
        if step.outcome is Outcome.ASK:
            return self._ask(run_id, step.say, step.questions, result)
        values = run_values.for_intake(self.connection, intake, known)
        values.update(
            {
                "agent_name": contact.name,
                "agent_email": contact.email,
                "agent_phone": contact.phone,
                "agent_title": contact.title,
            }
        )
        values["address"] = template_manifest.normalise_address(values.get("address", ""))
        values["hero_photo"] = self.hero_photo_url
        if not values.get("headshot"):
            values["headshot"] = self.headshot_for(values.get("agent_name", ""))

        self.progress("is measuring the design...")
        measured = self.preflight_template(
            template_id,
            template_label,
            step.category,
            resolution,
            values,
        )
        if measured.blockers:
            issue = measured.blockers[0]
            return self._ask(run_id, issue.say, [], result, status=issue.status)
        # Correctable layout work is Gable's job. Text is fitted to the largest
        # readable size and a supplied photo is center-cropped to the measured
        # frame; neither becomes another Slack question. The notes are folded
        # into the one post-build outcome, after the rendered vision gate.
        advisories = [safe(issue.advisory) for issue in measured.warnings if issue.advisory]

        # A listing flyer without its photograph is not a draft. The template
        # has already been checked, so the upload can resume this same run and
        # compare the photo against the measured frame before anything builds.
        if not self.hero_photo_url:
            return self._ask(
                run_id,
                "Can you send me the image?",
                [],
                result,
                status="needs_photo",
                headline=people.announce(self.connection, intake, contact.name),
            )

        # 6. Build only after all deterministic preflight checks pass.
        store.set_status(
            self.connection,
            run_id,
            "building",
            f"preflight passed for {template_label}",
        )
        run_notes = [safe(step.say)] if step.say else []

        # These designs differ; one global field set is what
        # shipped a flyer carrying the literal words "Phone" and "Website".
        manifest = template_manifest.manifest_for(template_label)
        field_problems = template_manifest.validate(manifest, values)
        blocking = [item for item in field_problems if item.blocking]
        if blocking:
            return self._ask(run_id, blocking[0].say, [], result)
        # The legacy manifest's character budgets are deliberately not spoken.
        # Exact source-box geometry was measured above; repeating an older
        # hand-entered character estimate can contradict that result. Required
        # fields remain the manifest's responsibility, while layout advice now
        # comes only from the measured preflight report.

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

        output_id, output_url = self.copy_template(template_id, run_values.output_name(intake))
        changed = self.fill(output_id, pairs)
        logger.info("run %s filled %d field(s)", run_id, changed)
        if not pairs or changed != len(pairs):
            # Two different situations. "No pairs" means the design has nothing
            # Gable recognises — the Client Review layouts are a client quote, a
            # client name and stars, with no address or price because there is
            # no property. Reporting that as a field failing to match sends
            # whoever reads it looking for a text bug that is not there.
            nothing_matched = not pairs
            store.set_status(
                self.connection,
                run_id,
                "needs_review",
                (
                    "this design has no fields Gable knows how to fill"
                    if nothing_matched
                    else "the template text did not match each intended field exactly once"
                ),
                output_file_id=output_id,
                output_url=output_url,
            )
            result.status = "needs_review"
            result.output_url = output_url
            spoken = safe(
                "I do not know how to fill this design — none of its text matches "
                "anything I recognise. It may need a kind of information I do not "
                "collect yet."
                if nothing_matched
                else "I copied the design, but one of its fields did not match exactly "
                "once. I stopped before changing any text."
            )
            result.said.append(spoken)
            posted_ts = self.say(spoken, self.origin_thread_ts or None)
            run_reporting.remember_thread(self.connection, run_id, posted_ts, self.origin_thread_ts)
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
        readback = run_reporting.read_back(self.read_slide_text, output_id)
        if readback is None:
            store.set_status(
                self.connection,
                run_id,
                "needs_review",
                "the filled flyer could not be read back for verification",
                output_file_id=output_id,
                output_url=output_url,
            )
            result.status = "needs_review"
            result.output_url = output_url
            spoken = safe(
                "I filled the design, but Google Slides did not let me read it back to "
                "verify the values. I have not sent it as finished."
            )
            result.said.append(spoken)
            posted_ts = self.say(spoken, self.origin_thread_ts or None)
            run_reporting.remember_thread(self.connection, run_id, posted_ts, self.origin_thread_ts)
            return result

        sent = {value for value in pairs.values() if value.strip()}
        wrong = audit.values_missing_from(readback, values, sent)
        stray: list[str] = []
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
            stray = audit.foreign_content_in(readback, values, run_values.OFFICE_PHONE)
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
            posted_ts = self.say(spoken, self.origin_thread_ts or None)
            run_reporting.remember_thread(self.connection, run_id, posted_ts, self.origin_thread_ts)
            return result

        self.progress("is placing the photo...")
        placed = self.place_photo(run_id, output_id, self.hero_photo_url, template_label)
        # The sample face is the most visible thing Gable gets wrong: one agent's
        # name beside another agent's photograph. Best effort — a design with no
        # headshot frame is still a deliverable flyer.
        headshot_url = values.get("headshot", "")
        headshot_failed = False
        if placed and headshot_url:
            self.progress("is putting the agent's face on it...")
            headshot_result = self.place_headshot(output_id, headshot_url)
            if headshot_result is True:
                logger.info("replaced the sample headshot for run %s", run_id)
            elif headshot_result is False:
                headshot_failed = True
                logger.error("could not replace the sample headshot for run %s", run_id)
            else:
                logger.info("the design has no recognised headshot slot for run %s", run_id)
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
            posted_ts = self.say(spoken, self.origin_thread_ts or None)
            run_reporting.remember_thread(self.connection, run_id, posted_ts, self.origin_thread_ts)
            return result
        if headshot_failed:
            store.set_status(
                self.connection,
                run_id,
                "needs_review",
                "the agent headshot could not be placed",
                output_file_id=output_id,
                output_url=output_url,
            )
            result.status = "needs_review"
            result.output_url = output_url
            spoken = safe(
                "I built the flyer but could not replace the sample headshot with the "
                "agent's own photo. I have not sent it as finished."
            )
            result.said.append(spoken)
            posted_ts = self.say(spoken, self.origin_thread_ts or None)
            run_reporting.remember_thread(self.connection, run_id, posted_ts, self.origin_thread_ts)
            return result

        # 7a. Fit the text to its boxes. Slides cannot autofit over the API —
        # verified: "Autofit types other than NONE are not supported" — so a
        # value longer than its placeholder clips silently. This is what shipped
        # a price reading $510,000 as $510,00.
        # Only the boxes this run filled. Fitting every box rewrote the design:
        # it shrank the "Just"/"Listed" headline by a third on a reviewed flyer,
        # pulling the two words apart and leaving the address high in a box
        # sized for larger type. The template is the specification.
        text_fit = run_reporting.fit_changed_text(
            self.read_text_boxes,
            self.apply,
            output_id,
            pairs,
            resolution,
            values,
        )
        if text_fit.count:
            logger.info("run %s refitted %d text box(es)", run_id, text_fit.count)
            advisories.append(safe(text_fit.note))

        # 7b. Check it twice: once on the text, once by looking at it. The text
        # pass verifies every value is PRESENT; only the vision pass can see
        # whether it FITS, and that gap delivered a clipped flyer once already.
        expected = {
            name: values[name]
            for name, literal in resolution.fields.items()
            if literal in pairs and values.get(name, "").strip()
        }
        final_text = run_reporting.read_back(self.read_slide_text, output_id)
        verdict = judge(final_text or "", expected, 1)
        seen: Inspection = self.look_at(run_id, self.thumbnail(output_id))

        problems = list(verdict.problems)
        if final_text is None:
            problems.insert(0, "the final text could not be read back for verification")
        if not seen.checked:
            problems.append("the visual inspection could not run")
        elif not seen.confident:
            problems.append("the visual inspection was inconclusive")
        elif not seen.looks_right:
            problems.extend(seen.problems or ["the visual inspection found a problem"])
        if text_fit.unreadable:
            problems.append(
                f"the {text_fit.unreadable[0].text[:24]} would have to be shrunk so far "
                "it would be hard to read"
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
            if not seen.checked:
                spoken = safe("I rendered it, but I could not complete the visual inspection.")
            elif not seen.confident:
                spoken = safe("I rendered it, but the visual inspection was inconclusive.")
            else:
                spoken = seen.say or verdict.say or safe(f"I rendered it, but {problems[0]}")
            message = safe(
                "\n".join(
                    [
                        spoken,
                        "I have not sent it as finished. I kept the supplied image and draft "
                        "so this run can be retried without starting over.",
                    ]
                )
            )
            result.said.append(message)
            posted_ts = self.say(message, self.origin_thread_ts or None)
            run_reporting.remember_thread(self.connection, run_id, posted_ts, self.origin_thread_ts)
            return result

        # `delivered` is persisted only after Slack confirms the link message.
        store.set_status(
            self.connection,
            run_id,
            "building",
            "flyer verified and waiting for its Slack delivery message",
            output_file_id=output_id,
            output_url=output_url,
        )
        result.output_url = output_url
        photo_note = run_reporting.photo_note(self.connection, run_id)
        missing_price_note = price_note(intake, "price" in resolution.fields)
        # Keep every useful outcome detail in the one link message.
        message = safe(
            "\n".join(
                [
                    f"Your flyer is ready. <{output_url}|Open the flyer>",
                    *run_notes,
                    *([photo_note] if photo_note else []),
                    *advisories,
                    *([missing_price_note] if missing_price_note else []),
                ]
            )
        )
        posted_ts = self.say(message, self.origin_thread_ts or None)
        if not posted_ts.strip():
            raise RuntimeError("Slack did not confirm the delivery message")
        thread_root = self.origin_thread_ts or posted_ts
        store.set_status(
            self.connection,
            run_id,
            "delivered",
            "Slack confirmed the delivery message",
            output_file_id=output_id,
            output_url=output_url,
            slack_thread_ts=thread_root,
            failure_reason="",
        )
        result.status = "delivered"
        result.said.append(message)
        return result

    def _ask(
        self,
        run_id: str,
        question: str,
        questions: list[Any],
        result: RunResult,
        status: str = "needs_info",
        headline: str = "",
        pending_warning_code: str = "",
    ) -> RunResult:
        """Stop the run and put a question in Slack, under a headline if given.

        A headline is announced as its own channel message and the question
        posted underneath it as a reply. That is not decoration: one combined
        message sits flat in the channel and starts no thread, and the photo
        handoff only accepts an upload that arrives inside the listing's thread,
        so a flat message leaves Carmen nowhere to put the photo.
        """
        asked = safe(question or "I need one more thing before I can build this.")
        if headline and not self.origin_thread_ts:
            root_ts = self.say(safe(headline), None)
            posted_ts = self.say(asked, root_ts or None)
            thread_root = root_ts or posted_ts
            result.said.append(safe(headline))
        else:
            posted_ts = self.say(asked, self.origin_thread_ts or None)
            thread_root = self.origin_thread_ts or posted_ts
        store.set_status(
            self.connection,
            run_id,
            status,
            asked[:200],
            slack_thread_ts=thread_root,
            failure_reason=asked[:400],
            pending_warning_code=pending_warning_code,
        )
        result.status = status
        result.said.append(asked)
        result.questions = [asked, *[q.ask for q in questions[1:]]]
        return result
