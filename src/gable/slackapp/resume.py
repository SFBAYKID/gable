"""Continue one paused listing from a reply in its own Slack thread.

Three conversational replies end in the same place — the person said the source
design was updated, answered the question Gable asked, or told it to build
without the values nobody has. Each refreshes the listing's sources and then
resumes that exact run, so the tail lives here once instead of three times in
``slackapp.runtime``.

Does not handle: deciding *whether* to resume, or recording what the reply
meant. Callers own that and pass an already-refreshed submission.
"""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection
from typing import Any

from gable.config import Settings
from gable.db import store
from gable.pipeline.live import build_runner
from gable.pipeline.questions import PostOnce, ReconcilePost
from gable.sheets import repository as repo


def resume_with_current_sources(
    *,
    settings: Settings,
    connection: Connection,
    drive: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    slides: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    stored: store.StoredSubmission,
    run: store.RunRow,
    thread_ts: str,
    progress: Callable[[str], None],
    post_once: PostOnce | None,
    reconcile: ReconcilePost | None,
) -> str:
    """Resume one paused run against sources already refreshed.

    Args:
        settings: Validated production settings.
        connection: Thread-owned database connection.
        drive: Drive v3 resource for this action.
        slides: Slides v1 resource for this action.
        stored: The submission re-read from the current sheet.
        run: The paused run being continued.
        thread_ts: The owned Slack thread.
        progress: Waiting-indicator seam.
        post_once: Durable outbox posting seam.
        reconcile: Bounded Slack history reader.

    Returns:
        Plain words for Slack, or empty when a durable outbox item has already
        said it and the listener must not repeat it.

    Raises:
        Nothing. The runner records its own failures.
    """
    captured: list[str] = []

    def capture(text: str, _requested_thread: str | None) -> str:
        captured.append(text)
        return thread_ts

    runner = build_runner(
        settings,
        connection,
        drive,
        slides,
        capture,
        post_once=post_once,
        reconcile=reconcile,
        hero_photo_url=run.photo_url,
        origin_thread_ts=thread_ts,
        progress=progress,
    )
    submission = repo.Submission(
        response_row_id=stored.response_row_id,
        sheet_row=stored.sheet_row,
        submitted_at=stored.submitted_at,
        intake=stored.intake,
        content_hash=stored.content_hash,
        source_tab=stored.source_tab,
    )
    result = runner.resume(submission, run.run_id)
    if captured:
        return captured[-1]
    if result.said:
        # Durable questions post through their idempotent outbox seam so SQLite
        # can confirm the exact Slack timestamp. The outer listener must not
        # post that same text again.
        return ""
    if store.has_pending_run_notification(connection, run.run_id):
        # A verified rebuild whose Slack acknowledgement was lost owns an exact
        # durable outcome. Never contradict it with the generic unchanged-flyer
        # fallback below.
        return ""
    return (
        "I picked this listing back up, but the run did not produce an outcome I "
        "could report. I left the listing paused."
        if result.needs_a_human
        else "I could not finish the rebuild, so I left the current flyer unchanged."
    )
