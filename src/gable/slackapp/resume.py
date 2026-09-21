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
from gable.pipeline.run_reporting import RunResult
from gable.sheets import repository as repo

#: What to say when a run is picked up again and stops on the same question it
#: has already asked and escalated. It reports the two things the person cannot
#: otherwise see -- that the current sources WERE re-read, and that the answer
#: is still not there -- and it does not repeat the question, which is the
#: whole point of the escalation. `run_speech.STUCK_CLOSING` already told this
#: thread that Chase has it.
ALREADY_ESCALATED: str = (
    "I read this listing's form row and contact record again just now, and I still do not "
    "have the one thing I asked for above, so it is still paused. I have already flagged "
    "this one for Chase rather than ask a third time."
)


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
    # An upload for this run that has been accepted but not finished is between
    # download and its own resume. Claiming the run here inside that window
    # wins — the photo question is already satisfied — so the rebuild ran
    # without the photograph and the upload's later claim lost, completing its
    # ingress with the photo dropped. Whoever sent both the photo and "run it
    # again" wants the run WITH the photo, so the photo goes first.
    if store.has_open_slack_event(connection, "file_share", run.run_id):
        return (
            "I am still fitting the photo that just arrived on this listing, "
            "so I did not start a second rebuild. It will finish on its own."
        )
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
    return silent_outcome_words(result)


def silent_outcome_words(result: RunResult) -> str:
    """What to say for a resumed run that finished without saying anything.

    Args:
        result: What the resumed run did.

    Returns:
        One sentence for the thread. Never empty: a person who asked for a
        rerun and is told nothing at all has no way to know it happened.

    Raises:
        Nothing.
    """
    if result.already_escalated:
        # The run stopped on the question it has already asked twice, so
        # `run_speech.repeat_guard` deliberately withheld a third copy. Saying
        # nothing USEFUL here is what made Ian DePinto's 2026-09-21 thread read
        # as a loop: two replies in a row of "the run did not produce an
        # outcome I could report", after Carmen had answered in the thread and
        # then put the value in the sheet. That sentence names nothing, so she
        # could not tell that her row HAD been re-read and still could not be
        # used -- which is the only thing she needed to know.
        return ALREADY_ESCALATED
    if result.needs_a_human:
        return (
            "I picked this listing back up, but the run did not produce an outcome I "
            "could report. I left the listing paused."
        )
    return "I could not finish the rebuild, so I left the current flyer unchanged."


def may_rebuild(
    connection: Connection,
    run: store.RunRow,
    action_id: str,
    thread_ts: str,
) -> bool:
    """Whether a run can now be rebuilt in its own thread.

    A finished flyer is terminal, so the claim inside a resume refuses it and
    the most natural thing to ask after reading one — "run it again, the price
    should be $560,000" — answered that nothing was waiting. Reopening it is
    what lets the reply be used.

    Args:
        connection: An open database connection.
        run: The run this thread owns.
        action_id: Stable Slack identity of the request, so a duplicate
            delivery cannot rebuild the same flyer twice.
        thread_ts: The thread the request arrived in.

    Returns:
        True when the run is already rebuildable or was reopened here. False
        when another delivery of the same request won the claim, in which case
        the caller must say nothing rather than contradict it.

    Raises:
        sqlite3.Error: on a write failure.
    """
    if run.status not in {"delivered", "needs_review"}:
        return True
    return store.reopen_for_rebuild(connection, run.run_id, action_id, thread_ts)
