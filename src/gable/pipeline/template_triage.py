"""Review newly uploaded source templates before a listing uses them."""

from __future__ import annotations

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

_TEMPLATE_NOTIFICATION_NAMESPACE = UUID("a5f075c1-55c9-46b8-893b-5326a29e4d87")


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
            message, status = self._review_item(
                item,
                duplicate=name_counts[self._key(item.name)] > 1,
                updated=updated,
            )
            thread_ts = existing.slack_thread_ts if existing is not None else ""
            store.record_template_audit(
                self.connection,
                item.file_id,
                item.name,
                item.modified_time,
                status,
                message,
                thread_ts,
                notification_pending=True,
            )
            checked += 1
            pending = store.template_audit(self.connection, item.file_id)
            if pending is not None:
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
    ) -> tuple[str, str]:
        """Measure one new or changed Drive revision and choose its verdict."""
        if not item.is_slides:
            return self._unsupported_message(item.name, updated=updated), "needs_template"
        if duplicate:
            return self._duplicate_message(item.name, updated=updated), "needs_template"
        report = self._inspect(item)
        visual = self.look_at(item.file_id) if not report.issues else None
        return self._message(
            item.name,
            report,
            updated=updated,
            visual=visual,
        )

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
        if not report.issues:
            progress("is inspecting the updated template...")
            visual = self.look_at(current.file_id)
        message, status = self._message(
            current.name,
            report,
            updated=True,
            visual=visual,
        )
        store.record_template_audit(
            self.connection,
            current.file_id,
            current.name,
            current.modified_time,
            status,
            message,
            existing.slack_thread_ts,
            notification_pending=notification_pending,
        )
        return message

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
    ) -> tuple[str, str]:
        """Choose one precise Slack outcome from a measured report."""
        issues = (*report.blockers, *report.warnings)
        if issues:
            message = issues[0].say
            if updated:
                message = message.replace("the new ", "the updated ", 1)
            return safe(message), "needs_template"
        timing = "updated" if updated else "new"
        if visual is None or not visual.checked:
            return (
                safe(
                    f"I measured the {timing} {name} design, but I could not complete "
                    "its visual inspection, so I have not certified it. Tell me to check "
                    "the template again."
                ),
                "needs_template",
            )
        if not visual.confident:
            return (
                safe(
                    f"I measured the {timing} {name} design, but the visual inspection "
                    "was inconclusive, so I have not certified it. Tell me to check the "
                    "template again."
                ),
                "needs_template",
            )
        if not visual.looks_right:
            problem = visual.problems[0] if visual.problems else "the visible layout looks wrong"
            problem = problem.strip().rstrip(".")
            problem = f"{problem[:1].lower()}{problem[1:]}"
            return (
                safe(
                    f"I inspected the {timing} {name} design, but {problem}. Fix that, "
                    "then tell me to check the template again."
                ),
                "needs_template",
            )
        prefix = "I read the updated" if updated else "I checked the new"
        message = safe(
            f"{prefix} {name} design from Generic Templates. I did not find a "
            "structural, text-capacity, or visible layout problem. I will still "
            "inspect each finished flyer before I call it ready."
        )
        return message, "ready"

    @staticmethod
    def _key(name: str) -> str:
        """Normalise a Drive file name for exact human-visible matching."""
        return " ".join(name.split()).casefold()

    @staticmethod
    def _unsupported_message(name: str, *, updated: bool) -> str:
        """Explain why a non-Slides upload cannot enter the pipeline."""
        timing = "updated" if updated else "new"
        return safe(
            f"I found the {timing} {name} file, but it is not a Google Slides design, "
            "so I cannot measure or build from it. Convert it to Google Slides in "
            "Generic Templates, then tell me to check it again."
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
