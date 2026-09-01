"""Run one form submission through prerequisite checks, build, vision, and Slack.

Every exit is recorded. Contradictory or unknowable source data pauses rather
than being guessed; correctable layout work is handled automatically.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any

from gable.agents import website as agent_website
from gable.db import store
from gable.listings.enrich import Facts
from gable.listings.intake import Intake, needs_two_agents, price_note
from gable.pipeline import (
    audit,
    needs,
    people,
    research_gate,
    resume_claim,
    run_images,
    run_reporting,
    run_speech,
    run_values,
)
from gable.pipeline import questions as run_questions
from gable.pipeline.contact_gate import ContactGate
from gable.pipeline.orchestrator import Outcome, agent_slots, judge, plan

# Re-exported: RunResult moved to run_reporting when this file reached the
# 800-line ceiling, so importers still say `from gable.pipeline.runner import`.
from gable.pipeline.run_reporting import RunResult as RunResult
from gable.pipeline.vision import Inspection, default_look_at
from gable.sheets import repository as repo
from gable.slides import fields as template_fields
from gable.slides import fitting, preflight
from gable.slides import manifest as template_manifest
from gable.voice import paragraphs, safe

logger = logging.getLogger("gable.runner")


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
    #: Posts one durable outbox item with Slack's stable client message id.
    #: Local/test runners may omit it and use the ordinary posting seam.
    post_once: run_questions.PostOnce | None = None
    reconcile: run_questions.ReconcilePost | None = None
    #: Refuses a source whose proactive template audit is still unresolved.
    #: Empty means the source is cleared for listing-specific preflight.
    template_clearance: Callable[[str, str], str] = lambda _fid, _label: ""
    #: Reads every text box with its size and geometry, for fitting.
    read_text_boxes: Callable[[str], list[fitting.TextBox]] = lambda _fid: []
    #: Reads one presentation whole, for the geometric audit that compares a
    #: built flyer with the design it was copied from.
    read_presentation: Callable[[str], dict[str, Any]] = lambda _fid: {}
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
    look_at: Callable[[str, bytes, tuple[str, ...]], Inspection] = default_look_at
    #: The hero photo for this listing, already fitted and published, or "" if
    #: none has been supplied yet.
    hero_photo_url: str = ""
    #: Root Slack thread for a resumed run. Replies return their own timestamp,
    #: but the run must keep this root so the next human response still maps.
    origin_thread_ts: str = ""
    #: Places the hero photo into a rendered flyer.
    place_photo: Callable[[str, str, str, str], bool] = lambda _run, _fid, _url, _template: False
    #: Puts the agent's own face where the sample face was. None means the
    #: design has no recognisable slot; False means a known slot was unsafe.
    place_headshot: Callable[[str, str, dict[str, str], str], bool | None] = (
        run_images.ignore_headshot
    )
    #: Proves that the photo URL is usable for the target slot. The live
    #: builder supplies the network checker; the runner itself performs no
    #: hidden I/O.
    check_photo: Callable[[str, str], tuple[bool, str]] = lambda _url, _slot: (True, "")
    #: Looks up public facts for an address.
    research: Callable[[str, frozenset[str]], Facts] = lambda _address, _fields: Facts()
    #: Checks the official brokerage profile only when the refreshed contact
    #: workbook has no complete, exact record. The unit-test default performs
    #: no hidden I/O; ``pipeline.live`` supplies the bounded website client.
    official_contact_lookup: Callable[[str, str, str], agent_website.ProfileLookup] = (
        lambda _name, _email, _phone: agent_website.unavailable_lookup()
    )
    #: The credential every agent at this brokerage holds, printed when a design
    #: has a title field and the agent's official profile leaves its job title
    #: blank. Empty means a blank profile title stops the run, as it used to.
    default_agent_credential: str = ""
    #: Publishes this agent's headshot and returns a URL Slides can fetch.
    #: Preflight stops if the design has a headshot well and this stays empty.
    headshot_for: Callable[[str], str] = lambda _name: ""
    #: Names the stage being worked on, for Slack's waiting indicator. Cosmetic
    #: by contract: it is called around real work and must never affect it.
    progress: Callable[[str], None] = lambda _stage: None

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
            return RunResult(
                run_id=latest.run_id if latest else "",
                status=latest.status if latest else "failed",
            )
        except store.RunLimitReachedError:
            latest = store.latest_run(self.connection, submission.response_row_id)
            result = RunResult(
                run_id=latest.run_id if latest else "",
                status="failed",
            )
            spoken = safe(run_reporting.RUN_LIMIT_REACHED)
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
        expected_status: str | None = None,
    ) -> RunResult:
        """Continue an existing paused run after Carmen supplies its photo.

        Args:
            submission: The locally stored intake row for the paused run.
            run_id: The existing run to continue; no new attempt is opened.
            resume_fields: Photo provenance or other run fields that must be
                committed atomically with the paused-run claim.
            expected_status: Optional exact paused state required by the claim.
                A photo upload passes the state it was accepted in, so a stale
                file event cannot resume a run that has since paused for
                another reason.

        Returns:
            What the resumed run did.

        Raises:
            Nothing. The same failure boundary as a new run records problems.
        """
        claim = resume_claim.claim_for_resume(
            self.connection,
            run_id,
            {"failure_reason": "", **(resume_fields or {})},
            expected_status=expected_status,
            origin_thread_ts=self.origin_thread_ts,
            photo_event_id=str((resume_fields or {}).get("photo_event_id") or ""),
        )
        if not claim.won:
            result = RunResult(run_id=run_id, status=claim.status)
            if claim.duplicate:
                return result
            spoken = safe(resume_claim.ALREADY_WORKING)
            result.said.append(spoken)
            self.say(spoken, self.origin_thread_ts or None)
            return result
        result = RunResult(run_id=run_id, status="pending")
        return self._perform(run_id, submission.intake, result)

    def _perform(self, run_id: str, intake: Intake, result: RunResult) -> RunResult:
        """Run the sequence behind one shared, state-recording failure boundary."""
        try:
            return self._sequence(run_id, intake, result)
        except Exception as error:
            logger.exception("run %s failed", run_id)
            # The kind of error, never its message, which can carry a signed
            # URL or an id. Three overnight runs failed here and recorded the
            # same sentence, so none could be told apart without reproducing it.
            reason = (
                f"a processing step failed before an outcome was confirmed ({type(error).__name__})"
            )
            current = store.run_by_id(self.connection, run_id)
            if current is not None and store.has_pending_run_notification(self.connection, run_id):
                result.status = current.status
                result.output_url = current.output_url
                return result
            spoken = safe(run_reporting.BUILD_STEP_FAILED)
            try:
                return self._outcome(
                    run_id,
                    spoken,
                    result,
                    status="failed",
                    detail=reason,
                )
            except Exception:
                logger.exception("could not persist the failure outcome for run %s", run_id)
                run_reporting.record_unhandled_failure(self.connection, run_id, reason)
            result.status = "failed"
            return result

    def _sequence(self, run_id: str, intake: Intake, result: RunResult) -> RunResult:
        """The ordered steps. Split out so `run` owns only the failure boundary."""
        self.progress("is checking the agent contact information...")
        contact_gate = ContactGate(
            self.connection,
            intake,
            self.official_contact_lookup,
            default_agent_credential=self.default_agent_credential,
        )
        contact = contact_gate.check(run_id)
        if not contact.ready:
            return self._ask(run_id, intake, contact.problem, [], result, status="needs_info")
        # Released by a person, or by Gable's own batched ask having already
        # gone out. Either way a value nobody has leaves the design's own
        # placeholder from here on, instead of asking for it a second time.
        allow_blank = store.blanks_approved(self.connection, run_id)
        # Outstanding requests are gathered rather than posted one at a time.
        # Chase's rule, 2026-08-13: one list, one round of answers, then build.
        outstanding = needs.Needs()

        # Contradictions are cheap and source-independent. They stop before
        # template reads, web calls, or Slack; property research deliberately
        # waits until the selected source reveals which facts it displays.
        step = plan(intake, required_public_facts=frozenset())
        if step.outcome is Outcome.ASK:
            stopping = [
                question for question in step.questions if not needs.joins_one_ask(question)
            ]
            if stopping:
                # A contradiction is not a gap. A price reduction with no new
                # price cannot be left as a placeholder and cannot be guessed
                # past, so it still stops here on its own.
                return self._ask(run_id, intake, stopping[0].ask, stopping, result)
            if not allow_blank:
                outstanding.add_values(
                    needs.still_unanswered(
                        step.questions,
                        store.recall_supplied_facts(self.connection, intake.address),
                    )
                )
        if step.outcome is Outcome.SKIP:
            store.set_status(self.connection, run_id, "skipped", step.detail)
            result.status = "skipped"
            if step.say:
                result.said.append(safe(step.say))
            return result

        # Two agents named with unclear roles is also a question.
        slots = agent_slots(intake)
        if slots.outcome is Outcome.ASK:
            return self._ask(run_id, intake, slots.say, slots.questions, result)

        if needs_two_agents(intake):
            return self._ask(
                run_id, intake, needs.TWO_AGENT_STOP, [], result, status="needs_template"
            )

        # Select the exact request-type source before either source-specific
        # credentials or public-property research. Its text decides whether a
        # REALTOR title, beds, baths, square footage, or price is needed. Core
        # contact validation already passed above; every remaining prerequisite
        # still precedes preflight, any Slack message, and every Drive copy.
        self.progress("is choosing the design...")
        template_id, template_label = self.pick_template(step.category, intake)
        if not template_id:
            return self._ask(
                run_id,
                intake,
                needs.missing_design(intake.request_type),
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
        # A credential printed from the brokerage default because the official
        # site did not answer is said once, with the link, not withheld.
        credential_note = ""
        if "agent_title" in resolution.fields:
            contact = contact_gate.check(run_id, require_title=True)
            if not contact.ready:
                return self._ask(run_id, intake, contact.problem, [], result, status="needs_info")
            credential_note = contact.note
        clearance_problem = self.template_clearance(template_id, template_label)
        if clearance_problem:
            return self._ask(
                run_id,
                intake,
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
            allow_blank_fields=allow_blank,
        )
        if step.outcome is Outcome.ASK:
            if not step.missing:
                # A researched number that looks wrong is not a gap either. It
                # gets confirmed before it reaches a flyer, on its own.
                return self._ask(run_id, intake, step.say, step.questions, result)
            outstanding.add_values(list(step.missing))
        values = run_values.assembled(
            self.connection,
            intake,
            known,
            agent_name=contact.name,
            agent_email=contact.email,
            agent_phone=contact.phone,
            agent_title=contact.title,
            hero_photo_url=self.hero_photo_url,
            headshot_for=self.headshot_for,
        )

        # The manifest is the authority on whether this design carries a property
        # photograph; `designs.NO_HERO_DESIGNS` records the measurement behind it.
        manifest_carries_a_photo = (
            template_manifest.manifest_for(template_label).find("hero_photo") is not None
        )

        self.progress("is measuring the design...")
        measured = self.preflight_template(
            template_id,
            template_label,
            step.category,
            resolution,
            values,
        )
        # A value that is about to be asked for in the same breath as the photo
        # must not stop preflight on the way there.
        gathering = allow_blank or bool(outstanding.values)
        # A value the measurement itself changed — a job title too long for the
        # slot the design drew, cut back to the credential inside it — must be
        # what gets filled. Measuring one string and writing another would put
        # the overflow back on the flyer.
        values.update(measured.adjusted)
        # A blocker used to return here with its own sentence and nothing else,
        # so Lina Mariner's New Listing asked for a headshot and never mentioned
        # the property photo it was equally certain to need. Carmen would have
        # answered, been asked again, and answered again. Blockers now join the
        # one batched ask below — every one of them, because reporting only the
        # first hides the second until the first is fixed.
        for issue in preflight.blocking_after_release(measured, gathering):
            outstanding.add_blocker(issue.say, issue.status)
        # Correctable layout work is Gable's job. Text is fitted to the largest
        # readable size and a supplied photo is center-cropped to the measured
        # frame; neither becomes another Slack question. The notes are folded
        # into the one post-build outcome, after the rendered vision gate.
        advisories = [safe(issue.advisory) for issue in measured.warnings if issue.advisory]
        if credential_note:
            advisories.append(safe(credential_note))

        # Asked WITH the photograph rather than one step after it; see
        # `manifest.needs_a_whole_address` for the round trip that cost.
        #
        # Asked ONCE, like every other value: this was missing the `allow_blank`
        # gate that guards the rest, so a listing answered with three of the four
        # values it was asked for came straight back with "I still need the
        # address". It only fires when an address IS supplied and is the wrong
        # shape, so after the ask, printing what the agent typed is the honest
        # move — "5556 Dolores Ave, 21227" is true and incomplete, a second
        # question is neither.
        if not allow_blank and template_manifest.needs_a_whole_address(
            template_manifest.manifest_for(template_label), values
        ):
            # Named precisely, not as a missing value: see
            # `needs.incomplete_address` for the announcement it contradicted.
            outstanding.add_blocker(needs.incomplete_address(values.get("address", "")))

        # A listing flyer without its photograph is not a draft. The template
        # has already been checked, so the upload can resume this same run and
        # compare the photo against the measured frame before anything builds.
        # Held, outstanding, or refused -- see `Needs.note_photo`. A repeated
        # blocker says "I have the property photo" so it does not read as though
        # the upload that just arrived went nowhere.
        #
        # Asked ONLY of a design that has somewhere to put one. This call was
        # unconditional until 2026-08-27, while the build below already guarded
        # its own hero work with `if hero_slot:` -- so the ask and the build
        # disagreed, and on Client Review Post the ask won. Carmen answered "there
        # is no property photo needed for this request", Gable asked again, and
        # nothing in Slack could release it: a missing photo is deliberately not
        # something `build_with_blank_fields` waives, and correctly so for a
        # design that really does have a photo well.
        if manifest_carries_a_photo:
            outstanding.note_photo(
                self.hero_photo_url,
                rejected=store.photo_was_rejected(self.connection, run_id),
            )

        # Everything Carmen could answer goes out in one message, once. She
        # replies once, and the next thing she sees is the link.
        if outstanding.anything:
            # What the ask commits to is written down from the words that
            # actually go out, never from the words that were intended --
            # see `run_speech.record_the_ask`.
            asked = run_speech.record_the_ask(self.connection, run_id, outstanding)
            # The announcement opens the thread, so it belongs only to the first
            # ask. A run resumed inside its own thread must not carry one: the
            # question store refuses a headline that would replace an existing
            # root, and the run died reporting a failed processing step. Seen
            # live when a date clarification resumed a run still owed its photo.
            return self._ask(
                run_id,
                intake,
                asked,
                [],
                result,
                status=outstanding.status(),
                headline=people.opening_for(
                    self.connection, intake, self.origin_thread_ts, contact.name
                ),
            )

        # Announced whether this ends in a question or a link: a run that never
        # had to ask owned no thread, and put its link at channel level.
        self.origin_thread_ts = people.open_thread(
            self.connection, run_id, intake, contact.name, self.say, self.origin_thread_ts
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
        # An absent value was already asked for in the one batched message. It
        # keeps the design's placeholder rather than stopping a finished flyer;
        # a value that is present and malformed still stops.
        blocking = [
            item
            for item in field_problems
            if item.blocking and not (allow_blank and (item.absent or item.releasable))
        ]
        if blocking:
            return self._ask(run_id, intake, blocking[0].say, [], result)
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
                    intake,
                    f"I could not use that photo — {why_not}. Could you send another?",
                    [],
                    result,
                    status="needs_photo",
                )

        pairs = template_fields.replacements(resolution, values)

        output_id, output_url = self.copy_template(template_id, run_values.output_name(intake))
        changed = self.fill(output_id, pairs)
        logger.info("run %s filled %d field(s)", run_id, changed)
        unfinished = run_reporting.fill_failure(pairs, changed)
        if unfinished is not None:
            return self._outcome(
                run_id,
                unfinished.spoken,
                result,
                status="needs_review",
                detail=unfinished.detail,
                output_file_id=output_id,
                output_url=output_url,
            )

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
            spoken = safe(run_reporting.UNREADABLE_FLYER)
            return self._outcome(
                run_id,
                spoken,
                result,
                status="needs_review",
                detail="the filled flyer could not be read back for verification",
                output_file_id=output_id,
                output_url=output_url,
            )

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
            detail = (
                f"a filled value did not read back correctly: {wrong[0]}"
                if wrong
                else f"contact details that are not this listing's: {stray[0]}"
            )[:400]
            # Two different problems, two different sentences. Reading Gable's
            # own words in Slack caught these being spliced together into "the
            # phone number 410.456.6868 is not this listing's on it does not
            # match what I was given" — one message built from a template that
            # expects a bare field name and a value that is already a sentence.
            spoken = safe(run_reporting.mismatch(wrong[0] if wrong else "", stray))
            return self._outcome(
                run_id,
                spoken,
                result,
                status="needs_review",
                detail=detail,
                output_file_id=output_id,
                output_url=output_url,
            )

        # Both photographs, and what to say when one did not land. The stage
        # lives in `run_images` so the difference between "this design has no
        # property photo" and "I could not place the property photo" is one
        # testable decision rather than three flags in the middle of a build.
        unplaced = run_images.place_all(
            run_id,
            output_id,
            template_label,
            self.hero_photo_url,
            values,
            carries_a_photo=manifest_carries_a_photo,
            place_photo=self.place_photo,
            place_headshot=self.place_headshot,
            progress=self.progress,
        )
        if unplaced:
            return self._outcome(
                run_id,
                safe(run_images.unfinished(unplaced)),
                result,
                status="needs_review",
                detail=f"the flyer was left unfinished: it {unplaced}",
                output_file_id=output_id,
                output_url=output_url,
            )

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

        # 7b. Measure what the build did to the design's own layout. A change
        # is SAID, under the link, not used to withhold the flyer. This used to
        # park the run in review, and on 2026-09-01 Brittney Bushee's Under
        # Contract flyer — built, linked in the database, one headshot twenty
        # points low — was described to Carmen instead of sent. She answered
        # "That's ok. Please send it and I can adjust" and was refused. Chase's
        # rule of 2026-08-20 is that Gable produces the flyer no matter what,
        # and his 2026-08-17 rule is that a flyer a check disliked still goes
        # out with what was noticed. A rectangle is no exception: she can fix
        # twenty points in Slides in less time than it takes to read about it.
        moved = run_reporting.layout_notice(
            self.read_presentation(template_id),
            self.read_presentation(output_id),
        )
        layout_noticed = ""
        if moved is not None:
            logger.warning("run %s moved the layout: %s", run_id, moved.detail)
            layout_noticed = moved.spoken

        # 7c. Check it twice: once on the text, once by looking at it.
        checked = run_reporting.verify_rendered(
            run_id,
            output_id,
            read_slide_text=self.read_slide_text,
            thumbnail=self.thumbnail,
            look_at=self.look_at,
            judge_text=judge,
            pairs=pairs,
            resolution=resolution,
            values=values,
            text_fit=text_fit,
            # When 7b found nothing, every rectangle matches the design and a
            # layout opinion from the render cannot be a defect Gable made.
            layout_verified=moved is None,
        )

        # A built flyer is delivered even when a check disliked it. Two real
        # listings were withheld over how the supplied photograph was cropped —
        # a roofline on one, a front entrance on the other — after Carmen had
        # sent every value and the image herself. She got a description of a
        # flyer she could not open, which leaves her with nothing to act on and
        # every reason to build it by hand instead. Withholding also assumes
        # Gable's judgement of her own photograph beats hers, which is backwards:
        # she reviews every post before a client sees it, so the honest move is
        # to send the flyer and say plainly what was noticed. Chase's call,
        # 2026-08-17, after seeing both threads. A rectangle 7b measured is
        # noticed the same way, since 2026-09-01.
        noticed_parts: list[str] = [layout_noticed]
        noticed_details: list[str] = [moved.detail] if moved is not None else []
        if not checked.ok:
            result.output_url = output_url
            # When another photograph is the whole remedy, the flyer still goes
            # out -- Chase's 2026-08-17 rule, unchanged -- but the run records
            # that a replacement is genuinely wanted, so the upload answering
            # the sentence below is accepted without needing magic words. The
            # verdict computed this and nothing read it, which made the one case
            # where a replacement is certain the one case that was hardest to
            # send. Set BEFORE the outcome, so a crash between them leaves a run
            # that accepts the photo rather than one that refuses it.
            if checked.needs_replacement_photo:
                store.set_awaiting_photo(self.connection, run_id, True)
            noticed_parts.extend(
                [checked.spoken, run_reporting.reframe_offer(manifest_carries_a_photo)]
            )
            noticed_details.append(checked.detail)
        # The verified file and exact link message are persisted before Slack.
        # Until Slack confirms the post the run remains building and the
        # process-lifetime outbox retries without rebuilding the flyer.
        # A value nobody supplied kept the design's placeholder rather than
        # stopping the flyer, so the delivery message says which ones.
        message = run_reporting.delivery_message(
            self.connection,
            run_id,
            output_url=output_url,
            run_notes=run_notes,
            advisories=advisories,
            left_blank=run_reporting.unfilled(resolution.fields, values),
            price_missing_note=price_note(intake, "price" in resolution.fields),
            noticed=safe(paragraphs(*noticed_parts)),
        )
        return self._outcome(
            run_id,
            message,
            result,
            status="delivered",
            pending_status="building",
            detail=(
                f"delivered with what the checks noticed: {'; '.join(noticed_details)}"[:400]
                if noticed_details
                else "flyer verified and waiting for its Slack delivery message"
            ),
            confirmation_detail="Slack confirmed the delivery message",
            output_file_id=output_id,
            output_url=output_url,
        )

    def _outcome(
        self,
        run_id: str,
        message: str,
        result: RunResult,
        *,
        status: str,
        detail: str,
        pending_status: str | None = None,
        confirmation_detail: str = "Slack confirmed the run outcome",
        output_file_id: str = "",
        output_url: str = "",
    ) -> RunResult:
        """Persist and attempt one exact non-question outcome message."""
        return run_speech.deliver_outcome(
            self.connection,
            self.say,
            run_id,
            message,
            result,
            status=status,
            detail=detail,
            thread_ts=self.origin_thread_ts,
            post_once=self.post_once,
            reconcile=self.reconcile,
            pending_status=pending_status,
            confirmation_detail=confirmation_detail,
            output_file_id=output_file_id,
            output_url=output_url,
        )

    def _ask(
        self,
        run_id: str,
        intake: Intake,
        question: str,
        questions: list[Any],
        result: RunResult,
        status: str = "needs_info",
        headline: str = "",
    ) -> RunResult:
        """Persist one question, announce the listing, and confirm the pause."""
        return run_speech.deliver_question(
            self.connection,
            self.say,
            run_id,
            intake,
            question,
            questions,
            result,
            status=status,
            headline=headline,
            thread_ts=self.origin_thread_ts,
            post_once=self.post_once,
            reconcile=self.reconcile,
        )
