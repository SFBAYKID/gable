"""Build and verify a versioned flyer edit before any canonical promotion.

The workflow is deliberately independent of Slack delivery and SQLite ledger
details.  A caller supplies one already claimed edit id and persists each
returned phase.  Gable copies the current delivered file, mutates only that
copy, proves deterministic readback first, applies safe text fitting when the
literal correction itself overflows, and then requires a checked, confident,
positive render inspection.

The result never claims that the run's canonical output changed.  Promotion is
the later durable-notification transaction: until Slack confirms the new link,
the original delivered presentation remains the run's output.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

from gable.pipeline.vision import Inspection
from gable.slides import fitting, preflight
from gable.slides.edit_plan import (
    EditPlanError,
    PlannedEdit,
    batch_was_confirmed,
    plan_edit,
    presentation_changed_only_as_planned,
)
from gable.voice import safe

logger = logging.getLogger("gable.edit.workflow")


@dataclass(frozen=True, slots=True)
class EditResult:
    """One non-promoted edit attempt and its precise human outcome."""

    status: str
    message: str
    draft_file_id: str = ""
    draft_url: str = ""
    plan: PlannedEdit | None = None
    visual: Inspection | None = None

    @property
    def verified(self) -> bool:
        """Whether this draft may be linked and promoted after Slack confirms."""
        return self.status == "verified" and bool(self.draft_file_id and self.draft_url)


CopyDraft = Callable[[str, str], tuple[str, str]]
ReadPresentation = Callable[[str], dict[str, Any]]
ApplyRequests = Callable[[str, list[dict[str, Any]]], dict[str, Any]]
RenderThumbnail = Callable[[str], bytes]
LookAt = Callable[[bytes], Inspection]


@dataclass
class EditWorkflow:
    """Execute a single safe edit against a separate Drive version."""

    copy_draft: CopyDraft
    read_presentation: ReadPresentation
    apply_requests: ApplyRequests
    thumbnail: RenderThumbnail
    look_at: LookAt

    def execute(
        self,
        source_file_id: str,
        edit_id: str,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        field_values: Mapping[str, Collection[str]] | None = None,
        required_values: Collection[str] = (),
        foreign_content: Callable[[str], Collection[str]] = lambda _text: (),
    ) -> EditResult:
        """Copy, apply, read back, render, and inspect one flyer change.

        Args:
            source_file_id: Current delivered presentation; never mutated.
            edit_id: Stable claimed action identity used for copy recovery.
            tool: Supported conversational edit tool.
            arguments: Exact validated request arguments.
            field_values: Known rendered values for semantic target resolution.
            required_values: Nonempty listing/contact values that must survive.
            foreign_content: Deterministic checker for wrong agent/contact data.

        Returns:
            Verified draft metadata, or a precise stopped result with no link.

        Raises:
            Nothing. Any external or validation failure leaves the canonical
            source untouched and becomes a truthful result.
        """
        draft_id = ""
        draft_url = ""
        plan: PlannedEdit | None = None
        try:
            before = self.read_presentation(source_file_id)
            plan = plan_edit(before, tool, arguments, field_values)
            draft_id, draft_url = self.copy_draft(source_file_id, edit_id)
            copied_before = self.read_presentation(draft_id)
            # Drive copied the same version we resolved. A source that changed
            # between reads invalidates target proof and must not be edited.
            if not _same_presentation(before, copied_before):
                return _stopped(
                    "The flyer changed while I was preparing a separate version, so I did not "
                    "apply the edit.",
                    draft_id,
                    plan,
                )
            reply = self.apply_requests(draft_id, list(plan.requests))
            if not batch_was_confirmed(plan, reply):
                return _stopped(
                    "Google Slides did not confirm the requested change, so I kept the current "
                    "flyer unchanged.",
                    draft_id,
                    plan,
                )
            after = self.read_presentation(draft_id)
            if not presentation_changed_only_as_planned(copied_before, after, plan):
                return _stopped(
                    "I could not prove that only the requested flyer element changed, so I kept "
                    "the current flyer unchanged.",
                    draft_id,
                    plan,
                )

            if plan.dynamic_text:
                after, fit_problem = self._fit_changed_text(after, draft_id, plan)
                if fit_problem:
                    return _stopped(fit_problem, draft_id, plan)

            text = "\n".join(item.text for item in preflight.text_boxes(after) if item.text.strip())
            expected_values = [
                plan.new_literal
                if plan.old_literal and value.strip() == plan.old_literal
                else value.strip()
                for value in required_values
            ]
            missing = [value for value in expected_values if value and value not in text]
            if missing:
                return _stopped(
                    "The edited version no longer contained every required listing value, so I "
                    "kept the current flyer unchanged.",
                    draft_id,
                    plan,
                )
            if foreign_content(text):
                return _stopped(
                    "The edited version contained contact details that do not belong to this "
                    "listing, so I kept the current flyer unchanged.",
                    draft_id,
                    plan,
                )
            rendered = self.thumbnail(draft_id)
            seen = self.look_at(rendered)
            if not seen.checked:
                message = (
                    "I made a separate edited version, but the visual inspection could not run, "
                    "so I kept the current flyer unchanged."
                )
            elif not seen.confident:
                message = (
                    "I made a separate edited version, but the visual inspection was "
                    "inconclusive, so I kept the current flyer unchanged."
                )
            elif not seen.looks_right:
                detail = seen.say or "I rendered the edited version, but it did not look right."
                message = f"{detail} I kept the current flyer unchanged."
            else:
                return EditResult(
                    "verified",
                    safe(f"{plan.success_message} <{draft_url}|Open the updated flyer>"),
                    draft_id,
                    draft_url,
                    plan,
                    seen,
                )
            return EditResult("needs_review", safe(message), draft_id, "", plan, seen)
        except EditPlanError as exc:
            logger.info("a conversational edit could not be resolved: %s", type(exc).__name__)
            return _stopped(_plan_message(str(exc)), draft_id, plan)
        except Exception:
            logger.exception("a versioned flyer edit failed before promotion")
            return _stopped(
                "I could not finish and verify a separate edited version, so I kept the current "
                "flyer unchanged.",
                draft_id,
                plan,
            )

    def _fit_changed_text(
        self,
        presentation: dict[str, Any],
        draft_id: str,
        plan: PlannedEdit,
    ) -> tuple[dict[str, Any], str]:
        """Fit only changed text, then prove the fit batch and readback."""
        boxes = [
            box
            for box in preflight.text_boxes(presentation)
            if box.object_id == plan.target_object_id and box.text == plan.dynamic_text
        ]
        if len(boxes) != 1:
            return presentation, (
                "I could not measure the changed text box exactly once, so I kept the current "
                "flyer unchanged."
            )
        fit = fitting.fit_for(
            boxes[0].object_id,
            boxes[0].text,
            boxes[0].font_size_pt,
            boxes[0].width_emu,
            boxes[0].lines,
            boxes[0].weight,
            boxes[0].family,
        )
        if fit.too_small_to_read:
            return presentation, (
                "The requested text would have to be smaller than the readability limit, so I "
                "kept the current flyer unchanged."
            )
        if plan.tool == "set_font_size" and fit.overflows:
            return presentation, (
                "The requested text size would not fit inside its current box, so I kept the "
                "current flyer unchanged."
            )
        requests = fitting.requests_for([fit])
        if not requests:
            return presentation, ""
        response = self.apply_requests(draft_id, requests)
        replies = response.get("replies", []) if isinstance(response, dict) else []
        if not isinstance(replies, list) or len(replies) != len(requests):
            return presentation, (
                "Google Slides did not confirm the automatic text fit, so I kept the current "
                "flyer unchanged."
            )
        refitted = self.read_presentation(draft_id)
        refit_boxes = [
            box
            for box in preflight.text_boxes(refitted)
            if box.object_id == plan.target_object_id and box.text == plan.dynamic_text
        ]
        if len(refit_boxes) != 1 or abs(refit_boxes[0].font_size_pt - fit.fitted_pt) > 0.011:
            return presentation, (
                "I could not verify the automatic text fit, so I kept the current flyer unchanged."
            )
        return refitted, ""


def _same_presentation(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Compare the source copy while ignoring expiring image delivery URLs."""

    def stable(value: Any) -> Any:  # noqa: ANN401 - recursive JSON value
        if isinstance(value, dict):
            return {
                key: stable(child)
                for key, child in value.items()
                if key not in {"contentUrl", "thumbnailUrl", "presentationId"}
            }
        if isinstance(value, list):
            return [stable(child) for child in value]
        return value

    # Copy-specific metadata such as presentationId and revisionId is expected
    # to differ. The one-slide visual structure and page size must not.
    visible_keys = ("slides", "pageSize")
    return bool(
        stable({key: first.get(key) for key in visible_keys})
        == stable({key: second.get(key) for key in visible_keys})
    )


def _stopped(
    message: str,
    draft_file_id: str,
    plan: PlannedEdit | None,
) -> EditResult:
    """Build one safe negative result that never exposes a draft link."""
    return EditResult("needs_review", safe(message), draft_file_id, "", plan)


def _plan_message(detail: str) -> str:
    """Translate known planner refusals without exposing code vocabulary."""
    if "exactly one" in detail:
        target = detail.removeprefix("could not identify exactly one ")
        return (
            f"I could not identify exactly one {target}, so I kept the current flyer unchanged. "
            "Tell me the exact text you mean."
        )
    if "already that size" in detail:
        return f"I checked the flyer and {detail}, so I did not create another version."
    return "I could not apply that change safely, so I kept the current flyer unchanged."
