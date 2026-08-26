"""Review newly uploaded source templates before a listing uses them."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from sqlite3 import Connection
from uuid import UUID, uuid5

from gable.db import store
from gable.listings.intake import REQUEST_TYPE_TO_CATEGORY
from gable.pipeline.questions import (
    PostOnce,
    ReconcilePost,
    notification_guard,
    post_persisted_notification,
)
from gable.pipeline.vision import Inspection
from gable.slides import preflight
from gable.slides.library import TemplateFile
from gable.voice import safe

logger = logging.getLogger("gable.template_triage")

_TEMPLATE_NOTIFICATION_NAMESPACE = UUID("a5f075c1-55c9-46b8-893b-5326a29e4d87")


@dataclass(frozen=True, slots=True)
class Verdict:
    """One template outcome: what to say, the status, and why it refuses."""

    message: str
    status: str
    blocker_kind: str = ""


def _listed(names: list[str]) -> str:
    """Join names the way a person writes a list."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _timing_free(message: str) -> str:
    """Reduce a verdict to its substance so two revisions can be compared.

    Only the words that say *when* Gable looked are removed. Two messages that
    survive this identically are the same verdict about the same fault, and
    saying it a second time tells Carmen nothing she does not already have in
    the thread.

    Args:
        message: A verdict exactly as it would be posted.

    Returns:
        The same sentence with its new/updated timing wording normalised.

    Raises:
        Nothing.
    """
    text = " ".join(message.split())
    for phrase in ("the updated ", "the new "):
        text = text.replace(phrase, "the ")
    # The clean verdict is the one place the timing word changes the verb.
    return text.replace("I read the ", "I checked the ", 1)


def _notification_key(file_id: str) -> str:
    """Return the process-local serialization key for one source template."""
    return f"template:{file_id}"


def _notification_client_id(audit: store.TemplateAudit) -> str:
    """Derive one stable Slack identity from the exact persisted revision."""
    identity = "\0".join((audit.file_id, audit.modified_time, audit.checked_at, audit.summary))
    return str(uuid5(_TEMPLATE_NOTIFICATION_NAMESPACE, identity))


def deliver_template_notification(
    connection: Connection,
    audit: store.TemplateAudit,
    say: Callable[[str, str | None], str],
    *,
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> bool:
    """Deliver one exact pending template verdict and confirm it atomically."""
    with notification_guard(_notification_key(audit.file_id)):
        current = store.template_audit(connection, audit.file_id)
        if (
            current is None
            or not current.notification_pending
            or current.modified_time != audit.modified_time
            or current.summary != audit.summary
            or current.checked_at != audit.checked_at
        ):
            return False
        posted_ts = post_persisted_notification(
            current.summary,
            current.slack_thread_ts or None,
            _notification_client_id(current),
            current.checked_at,
            current.notification_attempted_at,
            current.notification_attempt_count,
            say,
            claim=lambda expected_count, stale_before: store.claim_template_notification_delivery(
                connection,
                current,
                expected_count,
                stale_before,
            ),
            release=lambda token: store.release_template_notification_delivery(
                connection,
                current,
                token,
            ),
            post_once=post_once,
            reconcile=reconcile,
        )
        return store.confirm_template_notification(connection, current, posted_ts)


def drain_template_notifications(
    connection: Connection,
    say: Callable[[str, str | None], str],
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> int:
    """Attempt each stored template verdict without repeating its inspection."""
    return sum(
        deliver_template_notification(
            connection,
            audit,
            say,
            post_once=post_once,
            reconcile=reconcile,
        )
        for audit in store.pending_template_notifications(connection)
    )


@dataclass
class TemplateTriage:
    """Detect new Drive files, measure them, and own their Slack threads."""

    connection: Connection
    list_templates: Callable[[], list[TemplateFile]]
    read_presentation: Callable[[str], dict[str, object]]
    say: Callable[[str, str | None], str]
    post_once: PostOnce | None = None
    reconcile: ReconcilePost | None = None
    look_at: Callable[[str], Inspection] = lambda _file_id: Inspection(False, False, checked=False)
    slide_px: tuple[int, int] = (1080, 1350)

    def scan_new(self) -> int:
        """Adopt the first catalogue silently, then announce newly added files."""
        templates = self.list_templates()
        if not store.template_catalog_adopted(self.connection):
            # Production has a populated source catalogue. An empty first read
            # is indistinguishable from a missing/ambiguous folder or transient
            # Drive response, so do not baseline it and later announce every
            # established template as new.
            if not templates:
                return 0
            store.adopt_template_catalog(
                self.connection,
                [(item.file_id, item.name, item.modified_time) for item in templates],
            )
            return 0

        name_counts = Counter(self._key(item.name) for item in templates)
        checked = 0
        for item in templates:
            existing = store.template_audit(self.connection, item.file_id)
            revision_changed = existing is not None and item.modified_time != existing.modified_time
            if existing is not None and not revision_changed:
                # A prior inspection may have finished just before Slack was
                # unavailable. Retry only the stored message; never repeat the
                # paid visual call on every poll.
                if (
                    (existing.notification_pending or not existing.slack_thread_ts)
                    and existing.status != "baseline"
                    and existing.summary
                ):
                    deliver_template_notification(
                        self.connection,
                        existing,
                        self.say,
                        post_once=self.post_once,
                        reconcile=self.reconcile,
                    )
                continue
            updated = existing is not None
            verdict = self._review_item(
                item,
                duplicate=name_counts[self._key(item.name)] > 1,
                updated=updated,
                # A design Carmen has just edited is one she knows about. Only
                # a NEW design still announces a clean result.
                quiet_when_clean=updated,
            )
            message, status = verdict.message, verdict.status
            # Saving a file Gable has already refused, for the same reason,
            # produces a new revision and the identical sentence. Carmen was
            # told at 12:47 on 2026-08-26 that a .pptx is not a Google Slides
            # design, answered "I am working on it now", and was told the same
            # thing again at 12:51 because she had touched the file. Repeating
            # a verdict she is acting on is Gable talking over her. Record the
            # revision so clearance stays truthful, and stay quiet -- asking
            # for a recheck explicitly always answers, on a different path.
            if (
                existing is not None
                and message
                and existing.summary
                and status == existing.status
                and not existing.notification_pending
                and _timing_free(message) == _timing_free(existing.summary)
            ):
                logger.info(
                    "%s still has the same fault after an edit; not repeating it",
                    item.name,
                )
                store.record_template_audit(
                    self.connection,
                    item.file_id,
                    item.name,
                    item.modified_time,
                    status,
                    existing.summary,
                    existing.slack_thread_ts,
                    notification_pending=False,
                    blocker_kind=verdict.blocker_kind,
                )
                checked += 1
                continue
            thread_ts = existing.slack_thread_ts if existing is not None else ""
            store.record_template_audit(
                self.connection,
                item.file_id,
                item.name,
                item.modified_time,
                status,
                message,
                thread_ts,
                notification_pending=bool(message),
                blocker_kind=verdict.blocker_kind,
            )
            checked += 1
            pending = store.template_audit(self.connection, item.file_id)
            if pending is not None and message:
                deliver_template_notification(
                    self.connection,
                    pending,
                    self.say,
                    post_once=self.post_once,
                    reconcile=self.reconcile,
                )
        return checked

    def _review_item(
        self,
        item: TemplateFile,
        *,
        duplicate: bool,
        updated: bool,
        quiet_when_clean: bool = False,
    ) -> Verdict:
        """Measure one new or changed Drive revision and choose its verdict."""
        if not item.is_slides:
            # The naming note is already inside the unsupported message, which
            # is the one Carmen acts on by creating a differently named file.
            return Verdict(
                self._unsupported_message(item.name, updated=updated),
                "needs_template",
                store.BLOCKER_UNSUPPORTED,
            )
        if duplicate:
            return Verdict(
                self._duplicate_message(item.name, updated=updated),
                "needs_template",
                store.BLOCKER_DUPLICATE,
            )
        report = self._inspect(item)
        visual = self.look_at(item.file_id) if not report.blockers else None
        verdict = self._message(
            item.name,
            report,
            updated=updated,
            visual=visual,
            quiet_when_clean=quiet_when_clean,
        )
        note = self._naming_note(item.name)
        if note and verdict.message:
            verdict = replace(verdict, message=safe(verdict.message + note))
        return verdict

    def recheck_catalog(
        self,
        progress: Callable[[str], None] = lambda _stage: None,
    ) -> str:
        """Re-measure every design in Generic Templates and answer once.

        "I just imported new templates. Can you check again?" is the sentence a
        person actually says after replacing several files, and on 2026-08-26 it
        had nowhere to land: every recheck was keyed to a thread Gable already
        owned, so a top-level ask was answered "I could not match this thread to
        a listing or template". Carmen had six designs in flight and no way to
        ask about them together.

        A design refused only on how it LOOKS is reported as a note and still
        counts as buildable, for the reason in `placement.template_clearance`:
        the finished flyer is inspected on its own render either way.

        Args:
            progress: Truthful native-status stage reporter.

        Returns:
            One message naming what is ready and what still needs work.

        Raises:
            Nothing. A Drive or Slides failure surfaces as that design's own
            refusal rather than ending the sweep.
        """
        templates = self.list_templates()
        if not templates:
            return safe(
                "I could not find any designs in Generic Templates, so I have not "
                "changed anything. Put them in that folder and ask me again."
            )
        name_counts = Counter(self._key(item.name) for item in templates)
        ready: list[str] = []
        blocked: list[str] = []
        notes: list[str] = []
        for item in sorted(templates, key=lambda entry: entry.name.casefold()):
            progress(f"is checking {item.name}...")
            existing = store.template_audit(self.connection, item.file_id)
            try:
                verdict = self._review_item(
                    item,
                    duplicate=name_counts[self._key(item.name)] > 1,
                    updated=existing is not None,
                )
            except Exception:
                logger.exception("re-measuring %s failed", item.name)
                blocked.append(
                    safe(f"I could not read the {item.name} design, so I have not certified it.")
                )
                continue
            store.record_template_audit(
                self.connection,
                item.file_id,
                item.name,
                item.modified_time,
                verdict.status,
                verdict.message,
                existing.slack_thread_ts if existing is not None else "",
                notification_pending=False,
                blocker_kind=verdict.blocker_kind,
            )
            if verdict.blocker_kind == store.BLOCKER_VISUAL:
                ready.append(item.name)
                notes.append(verdict.message)
            elif verdict.status == "ready":
                ready.append(item.name)
            else:
                blocked.append(verdict.message)
        return self._catalog_answer(ready, blocked, notes)

    @staticmethod
    def _catalog_answer(ready: list[str], blocked: list[str], notes: list[str]) -> str:
        """Compose one plain answer to a whole-folder recheck."""
        parts: list[str] = []
        if ready:
            parts.append(f"These designs are ready to build from: {_listed(ready)}.")
        if blocked:
            parts.append("\n\n".join(blocked))
        if notes:
            parts.append(
                "I also want to flag how these look, though I will still build on them "
                "and inspect every finished flyer:\n\n" + "\n\n".join(notes)
            )
        if not parts:
            parts.append("I could not certify any of the designs in Generic Templates.")
        return safe("\n\n".join(parts))

    def recheck(
        self,
        thread_ts: str,
        progress: Callable[[str], None] = lambda _stage: None,
    ) -> str:
        """Reload and remeasure the source file owned by one Slack thread."""
        existing = store.template_for_thread(self.connection, thread_ts)
        if existing is None:
            return "I could not match this thread to a template, so I have not changed anything."
        return self._recheck(existing, progress)

    def recheck_action(
        self,
        thread_ts: str,
        action_id: str,
        progress: Callable[[str], None] = lambda _stage: None,
    ) -> str:
        """Claim, inspect, persist, and deliver one template-thread recheck."""
        existing = store.template_for_thread(self.connection, thread_ts)
        if existing is None:
            return "I could not match this thread to a template, so I have not changed anything."
        if not store.claim_slack_event(
            self.connection,
            "template_recheck",
            action_id,
            existing.file_id,
            thread_ts,
            existing.modified_time,
        ):
            pending = store.template_audit(self.connection, existing.file_id)
            if pending is not None and pending.notification_pending:
                deliver_template_notification(
                    self.connection,
                    pending,
                    self.say,
                    post_once=self.post_once,
                    reconcile=self.reconcile,
                )
            return ""
        outcome = self._recheck(existing, progress, notification_pending=True)
        pending = store.template_audit(self.connection, existing.file_id)
        if pending is not None and pending.notification_pending:
            deliver_template_notification(
                self.connection,
                pending,
                self.say,
                post_once=self.post_once,
                reconcile=self.reconcile,
            )
        store.complete_slack_event(
            self.connection,
            "template_recheck",
            action_id,
            existing.file_id,
            "template verdict persisted for durable delivery",
        )
        return "" if pending is not None else outcome

    def recheck_file(
        self,
        file_id: str,
        progress: Callable[[str], None] = lambda _stage: None,
    ) -> str:
        """Reload one audited source selected from a paused listing thread.

        Args:
            file_id: Drive id already recorded on the paused listing run.
            progress: Truthful native-status stage reporter.

        Returns:
            The same precise verdict as a source-template-thread recheck.

        Raises:
            Nothing. Missing audit state becomes a plain refusal.
        """
        existing = store.template_audit(self.connection, file_id)
        if existing is None:
            # The catalogue is baselined by a scheduled scan, and a listing can
            # reach this point before that scan has ever run — which is exactly
            # the state the live database was in on 2026-08-14, so Chase edited
            # the Open House design, asked for a rebuild, and was told the
            # design was not a reviewed source. A design Gable itself selected
            # and built this flyer from is not an unknown file. Measure it now
            # rather than refusing over a scan nobody has performed yet.
            current = next(
                (item for item in self.list_templates() if item.file_id == file_id),
                None,
            )
            if current is None:
                return (
                    "I could not find this listing's design in Generic Templates, so I "
                    "have not changed anything. Put it back in that folder and ask me again."
                )
            existing = store.TemplateAudit(
                file_id=current.file_id,
                name=current.name,
                modified_time="",
                status="needs_template",
            )
        return self._recheck(existing, progress, for_listing=True)

    def _recheck(
        self,
        existing: store.TemplateAudit,
        progress: Callable[[str], None],
        *,
        notification_pending: bool = False,
        for_listing: bool = False,
    ) -> str:
        """Reload, measure, and persist one already-resolved source audit.

        Args:
            existing: The audit being refreshed, real or provisional.
            progress: Truthful native-status stage reporter.
            notification_pending: Whether the verdict owes a durable Slack post.
            for_listing: True when a listing thread asked, in which case the
                new-design character allowances do not apply — see below.
        """
        templates = self.list_templates()
        current = next(
            (item for item in templates if item.file_id == existing.file_id),
            None,
        )
        if current is None:
            if existing.blocker_kind == store.BLOCKER_UNSUPPORTED or existing.status == "retired":
                # Gable asked for this file to be replaced with a Slides design.
                # Removing it is the fix, so demanding it back is Gable undoing
                # its own instruction -- which is what Carmen was told at 13:08
                # on 2026-08-26 after she correctly deleted a converted .pptx.
                message = safe(
                    f"The {existing.name} file is out of Generic Templates now, which is "
                    "what I asked for. There is nothing left to check on it."
                )
                store.record_template_audit(
                    self.connection,
                    existing.file_id,
                    existing.name,
                    existing.modified_time,
                    "retired",
                    message,
                    existing.slack_thread_ts,
                    notification_pending=notification_pending,
                )
                return message
            message = (
                f"I could not find the {existing.name} design in Generic Templates, so I "
                "could not check the update. Put it back in that folder and ask me again."
            )
            # A deleted or moved source must revoke an older ready verdict. The
            # picker also fails closed, but persisting the real state keeps
            # listing clearance truthful between scans.
            store.record_template_audit(
                self.connection,
                existing.file_id,
                existing.name,
                existing.modified_time,
                "needs_template",
                message,
                existing.slack_thread_ts,
                notification_pending=notification_pending,
                blocker_kind=store.BLOCKER_MISSING,
            )
            return message
        if not current.is_slides:
            message = self._unsupported_message(current.name, updated=True)
            store.record_template_audit(
                self.connection,
                current.file_id,
                current.name,
                current.modified_time,
                "needs_template",
                message,
                existing.slack_thread_ts,
                notification_pending=notification_pending,
                blocker_kind=store.BLOCKER_UNSUPPORTED,
            )
            return message
        if sum(self._key(item.name) == self._key(current.name) for item in templates) > 1:
            message = self._duplicate_message(current.name, updated=True)
            store.record_template_audit(
                self.connection,
                current.file_id,
                current.name,
                current.modified_time,
                "needs_template",
                message,
                existing.slack_thread_ts,
                notification_pending=notification_pending,
                blocker_kind=store.BLOCKER_DUPLICATE,
            )
            return message
        report = self._inspect(current)
        if for_listing:
            # The character allowances in TEMPLATE_CAPACITY_CHARS are the
            # standard for adopting a NEW design: could this box hold a
            # long-but-normal value from anyone on the roster. They are not a
            # reason to refuse rebuilding a listing that has already been built
            # on this very design — Tambria Eaton's Open House flyer was refused
            # over a hypothetical 28-character agent name, and recording that as
            # the design's verdict would then have blocked every Open House run.
            # This listing's own values are measured again by the runner before
            # anything is copied, which is the check that actually applies here.
            report = replace(
                report,
                issues=tuple(
                    issue for issue in report.issues if not issue.code.startswith("capacity_")
                ),
            )
        visual: Inspection | None = None
        # A listing rebuild reloads the design's current bytes and confirms it
        # is still structurally safe to fill. Certifying how the artwork LOOKS
        # is the new-design question, and asking it here refused to rebuild
        # Kirby-Jay John's flyer because the open-house tag on New Listing with
        # Open House hangs off the right edge — which it does on purpose, and
        # which the flyer that had already delivered showed. The flyer's own
        # render is inspected either way, so this also saves a paid call.
        if not report.blockers and not for_listing:
            progress("is inspecting the updated template...")
            visual = self.look_at(current.file_id)
        verdict = self._message(
            current.name,
            report,
            updated=True,
            visual=visual,
            visual_required=not for_listing,
        )
        note = self._naming_note(current.name)
        if note and verdict.message:
            verdict = replace(verdict, message=safe(verdict.message + note))
        store.record_template_audit(
            self.connection,
            current.file_id,
            current.name,
            current.modified_time,
            verdict.status,
            verdict.message,
            existing.slack_thread_ts,
            notification_pending=notification_pending,
            blocker_kind=verdict.blocker_kind,
        )
        return verdict.message

    def _inspect(self, template: TemplateFile) -> preflight.Report:
        """Apply structural and capacity checks to the current Drive revision."""
        category = REQUEST_TYPE_TO_CATEGORY.get(self._key(template.name), template.name)
        presentation = self.read_presentation(template.file_id)
        return preflight.certify(
            presentation,
            template.name,
            category,
            slide_px=self.slide_px,
        )

    @staticmethod
    def _message(
        name: str,
        report: preflight.Report,
        *,
        updated: bool,
        visual: Inspection | None = None,
        visual_required: bool = True,
        quiet_when_clean: bool = False,
    ) -> Verdict:
        """Choose one precise Slack outcome from a measured report.

        With ``quiet_when_clean``, a design Gable re-read on its own and found
        nothing wrong with returns no message at all. Carmen edited three
        designs in four minutes on 2026-08-19 and got three notifications
        saying nothing had happened, in the channel where real listings arrive.
        Nobody asked Gable to look, so a clean answer is not news. Every
        problem, every warning, and every design Gable is *asked* to check
        still speaks.

        Every refusal also records WHY it refuses. A structural fault makes the
        design unfillable and must stop a listing; a judgement about how the
        artwork looks is the design thread's question and must not, because the
        finished flyer is inspected on its own render either way.
        """
        if report.blockers:
            message = report.blockers[0].say
            if updated:
                message = message.replace("the new ", "the updated ", 1)
            return Verdict(safe(message), "needs_template", store.BLOCKER_STRUCTURAL)
        timing = "updated" if updated else "new"
        if not visual_required:
            return Verdict(
                safe(
                    f"I read the {timing} {name} design from Generic Templates and found "
                    "no structural or text-capacity problem. I will inspect the finished "
                    "flyer before I call it ready."
                ),
                "ready",
            )
        if visual is None or not visual.checked:
            return Verdict(
                safe(
                    f"I measured the {timing} {name} design, but I could not complete "
                    "its visual inspection, so I have not certified it. Tell me to check "
                    "the template again."
                ),
                "needs_template",
                store.BLOCKER_VISUAL,
            )
        if not visual.confident:
            return Verdict(
                safe(
                    f"I measured the {timing} {name} design, but the visual inspection "
                    "was inconclusive, so I have not certified it. Tell me to check the "
                    "template again."
                ),
                "needs_template",
                store.BLOCKER_VISUAL,
            )
        if not visual.looks_right:
            problem = visual.problems[0] if visual.problems else "the visible layout looks wrong"
            problem = problem.strip().rstrip(".")
            problem = f"{problem[:1].lower()}{problem[1:]}"
            return Verdict(
                safe(
                    f"I inspected the {timing} {name} design, but {problem}. Fix that, "
                    "then tell me to check the template again."
                ),
                "needs_template",
                store.BLOCKER_VISUAL,
            )
        # A measured tradeoff is worth saying and is not a reason to refuse the
        # design. Carmen hears that a slot is tight; the listings built on it
        # still get their own exact measurement before anything is copied.
        if report.warnings:
            return Verdict(
                safe(report.warnings[0].say.replace("the new ", f"the {timing} ", 1)),
                "ready",
            )
        if quiet_when_clean:
            return Verdict("", "ready")
        prefix = "I read the updated" if updated else "I checked the new"
        message = safe(
            f"{prefix} {name} design from Generic Templates. I did not find a "
            "structural, text-capacity, or visible layout problem. I will still "
            "inspect each finished flyer before I call it ready."
        )
        return Verdict(message, "ready")

    @staticmethod
    def _key(name: str) -> str:
        """Normalise a Drive file name for exact human-visible matching."""
        return " ".join(name.split()).casefold()

    @staticmethod
    def _naming_note(name: str) -> str:
        """Say when a file's name means no submission will ever select it.

        `slides.selection.template_picker` matches a design by exact request
        type: the folder holds one file per thing the form can ask for, named
        what the form calls it. A file named anything else is invisible to
        every listing, and saying so is the difference between Carmen finishing
        the job and Carmen doing work that changes nothing. On 2026-08-26 she
        was told to convert `Brittany Tawney Static.pptx`, which would have
        produced a perfectly good design named `Brittany Tawney Static` that
        Gable could never have picked.

        Args:
            name: The Drive file name, with or without an extension.

        Returns:
            One sentence to append, or empty when the name is a request type.

        Raises:
            Nothing.
        """
        stem = name.rsplit(".", 1)[0] if "." in name else name
        if " ".join(stem.split()).casefold() in REQUEST_TYPE_TO_CATEGORY:
            return ""
        return (
            f" Name it exactly what the form calls the request type as well, because "
            f"nothing on the form asks for {stem}, so I would never pick this design."
        )

    @staticmethod
    def _unsupported_message(name: str, *, updated: bool) -> str:
        """Explain why a non-Slides upload cannot enter the pipeline."""
        timing = "updated" if updated else "new"
        return safe(
            f"I found the {timing} {name} file, but it is not a Google Slides design, "
            "so I cannot measure or build from it. Convert it to Google Slides in "
            "Generic Templates, then tell me to check it again." + TemplateTriage._naming_note(name)
        )

    @staticmethod
    def _duplicate_message(name: str, *, updated: bool) -> str:
        """Explain a current source-name collision without choosing a file."""
        timing = "updated" if updated else "new"
        return safe(
            f"I found the {timing} design named {name}, but another file has the same "
            "name. I cannot choose safely between them. Rename or remove one, then "
            "tell me to check the template again."
        )
