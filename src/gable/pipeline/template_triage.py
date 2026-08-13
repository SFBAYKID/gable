"""Review newly uploaded source templates before a listing uses them."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from sqlite3 import Connection

from gable.db import store
from gable.listings.intake import REQUEST_TYPE_TO_CATEGORY
from gable.pipeline.vision import Inspection
from gable.slides import preflight
from gable.slides.library import TemplateFile
from gable.voice import safe


@dataclass
class TemplateTriage:
    """Detect new Drive files, measure them, and own their Slack threads."""

    connection: Connection
    list_templates: Callable[[], list[TemplateFile]]
    read_presentation: Callable[[str], dict[str, object]]
    say: Callable[[str, str | None], str]
    look_at: Callable[[str], Inspection] = lambda _file_id: Inspection(True, True)
    slide_px: tuple[int, int] = (1080, 1350)

    def scan_new(self) -> int:
        """Adopt the first catalogue silently, then announce newly added files."""
        templates = self.list_templates()
        if not store.template_catalog_adopted(self.connection):
            store.adopt_template_catalog(
                self.connection,
                [(item.file_id, item.name, item.modified_time) for item in templates],
            )
            return 0

        name_counts = Counter(self._key(item.name) for item in templates)
        checked = 0
        for item in templates:
            existing = store.template_audit(self.connection, item.file_id)
            if existing is not None:
                # A prior inspection may have finished just before Slack was
                # unavailable. Retry only the stored message; never repeat the
                # paid visual call on every poll.
                if (
                    existing.status != "baseline"
                    and not existing.slack_thread_ts
                    and existing.summary
                ):
                    thread_ts = self.say(existing.summary, None)
                    if thread_ts:
                        store.record_template_audit(
                            self.connection,
                            existing.file_id,
                            existing.name,
                            existing.modified_time,
                            existing.status,
                            existing.summary,
                            thread_ts,
                        )
                continue
            if not item.is_slides:
                message = self._unsupported_message(item.name, updated=False)
                status = "needs_template"
            elif name_counts[self._key(item.name)] > 1:
                message = self._duplicate_message(item.name, updated=False)
                status = "needs_template"
            else:
                report = self._inspect(item)
                visual = self.look_at(item.file_id) if not report.issues else None
                message, status = self._message(
                    item.name,
                    report,
                    updated=False,
                    visual=visual,
                )
            store.record_template_audit(
                self.connection,
                item.file_id,
                item.name,
                item.modified_time,
                status,
                message,
            )
            checked += 1
            thread_ts = self.say(message, None)
            if thread_ts:
                store.record_template_audit(
                    self.connection,
                    item.file_id,
                    item.name,
                    item.modified_time,
                    status,
                    message,
                    thread_ts,
                )
        return checked

    def recheck(
        self,
        thread_ts: str,
        progress: Callable[[str], None] = lambda _stage: None,
    ) -> str:
        """Reload and remeasure the source file owned by one Slack thread."""
        existing = store.template_for_thread(self.connection, thread_ts)
        if existing is None:
            return "I could not match this thread to a template, so I have not changed anything."
        templates = self.list_templates()
        current = next(
            (item for item in templates if item.file_id == existing.file_id),
            None,
        )
        if current is None:
            return (
                f"I could not find the {existing.name} design in Generic Templates, so I "
                "could not check the update. Put it back in that folder and ask me again."
            )
        if not current.is_slides:
            message = self._unsupported_message(current.name, updated=True)
            store.record_template_audit(
                self.connection,
                current.file_id,
                current.name,
                current.modified_time,
                "needs_template",
                message,
                thread_ts,
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
                thread_ts,
            )
            return message
        report = self._inspect(current)
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
            thread_ts,
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
