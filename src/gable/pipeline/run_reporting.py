"""Small reporting helpers shared by the flyer runner's outcome branches."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any

from gable.db import store
from gable.slides import fields as template_fields
from gable.slides import fitting, preflight

logger = logging.getLogger("gable.runner")


@dataclass(frozen=True, slots=True)
class TextFitResult:
    """Actual Slides text-size changes and any unreadable fit."""

    count: int = 0
    unreadable: tuple[fitting.Fit, ...] = ()
    note: str = ""


def fit_changed_text(
    read_text_boxes: Callable[[str], list[fitting.TextBox]],
    apply: Callable[[str, list[dict[str, Any]]], None],
    output_id: str,
    pairs: Mapping[str, str],
    resolution: template_fields.Resolution,
    values: Mapping[str, str],
) -> TextFitResult:
    """Resize filled text boxes and describe only changes Slides received."""
    single_line = {
        values.get(name, "").strip()
        for name in preflight.SINGLE_LINE_FIELDS
        if name in resolution.fields and values.get(name, "").strip()
    }
    fits = fitting.plan_fits(
        read_text_boxes(output_id),
        dynamic=pairs.values(),
        single_line=single_line,
    )
    shrunk = [fit for fit in fits if fit.overflows]
    unreadable = tuple(fit for fit in shrunk if fit.too_small_to_read)
    applied = [fit for fit in shrunk if not fit.too_small_to_read]
    if applied:
        apply(output_id, fitting.requests_for(applied))

    field_by_value: dict[str, list[str]] = {}
    for name in resolution.fields:
        value = values.get(name, "").strip()
        if value:
            field_by_value.setdefault(value, []).append(name.replace("_", " "))
    adjusted: list[str] = []
    for fit in applied:
        for name in field_by_value.get(fit.text.strip(), []):
            if name not in adjusted:
                adjusted.append(name)
    if not applied:
        note = ""
    elif not adjusted:
        note = "I reduced the filled text sizes to keep them inside their template boxes."
    else:
        names = (
            adjusted[0] if len(adjusted) == 1 else f"{', '.join(adjusted[:-1])} and {adjusted[-1]}"
        )
        noun = "size" if len(adjusted) == 1 else "sizes"
        pronoun = "it" if len(adjusted) == 1 else "them"
        note = f"I reduced the {names} text {noun} to keep {pronoun} inside the template boxes."
    return TextFitResult(len(applied), unreadable, note)


def read_back(read_slide_text: Callable[[str], list[str]], file_id: str) -> str | None:
    """Return all rendered flyer text, or ``None`` when verification failed."""
    try:
        return "\n".join(read_slide_text(file_id))
    except Exception:
        logger.exception("could not read the flyer back for verification")
        return None


def photo_note(connection: Connection, run_id: str) -> str:
    """Describe only the photo processing that actually happened."""
    row = connection.execute(
        "SELECT photo_source, ai_enhanced FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row or row["photo_source"] not in {"carmen", "slack_upload"}:
        return ""
    if int(row["ai_enhanced"] or 0):
        return "I sharpened, enlarged, and fitted the photo and finished the flyer."
    return "I resized and fitted the photo and finished the flyer."


def remember_thread(
    connection: Connection,
    run_id: str,
    posted_ts: str,
    origin_thread_ts: str = "",
) -> None:
    """Attach a run to the root Slack thread where its outcome was discussed."""
    root = origin_thread_ts or posted_ts
    if not root:
        return
    store.set_status(
        connection,
        run_id,
        status_of(connection, run_id),
        "thread recorded",
        slack_thread_ts=root,
    )


def status_of(connection: Connection, run_id: str) -> str:
    """Return the current run status, failing closed to human review."""
    try:
        row = connection.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    except Exception:
        return "needs_review"
    return str(row["status"]) if row else "needs_review"
