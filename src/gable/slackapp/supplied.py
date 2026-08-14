"""Turning an answer to Gable's one question back into a moving run.

Answering the question Gable asked has to move the run, or the question is a
dead end. It has been one twice: Chase replied "List price is $200,000" and
Gable thanked him, kept the run at `needs_info` and stored nothing; and on
2026-08-14 he answered "It's 1011 Winged Foot Dr, Bowie, MD 20721" to a question
about the address and the same thing happened, because no tool could carry an
address at all.

So this is one step, not two: record every value the reply carried, reload the
submission the correction may have changed, and continue the paused run from its
current sources.

Does not handle: reading a reply as an answer in the first place — that is the
conversational model's job — or any other conversational tool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from sqlite3 import Connection
from typing import Any, Protocol

from gable.config import Settings
from gable.db import store
from gable.sheets.client import SheetClient
from gable.slackapp.answers import record_stated
from gable.slackapp.source_refresh import SourceRefreshError, refresh_submission_sources

logger = logging.getLogger("gable.slack.supplied")

NO_LISTING: str = "I could not match this thread to a listing, so I have not recorded that."
NO_SOURCES: str = (
    "I could not refresh this listing from its form and contact record, so I have not "
    "recorded that and left the run paused."
)
NOT_UNDERSTOOD: str = (
    "I did not understand that as one of the details I asked for, so I have not recorded it."
)


class ResumesRun(Protocol):
    """Continues one paused run from its freshly re-read sources."""

    def __call__(  # noqa: D102 - the protocol's own docstring describes it
        self,
        *,
        settings: Settings,
        connection: Connection,
        drive: Any,  # noqa: ANN401 - googleapiclient resource
        slides: Any,  # noqa: ANN401 - googleapiclient resource
        stored: store.StoredSubmission,
        run: store.RunRow,
        thread_ts: str,
        progress: Callable[[str], None],
        post_once: Any,  # noqa: ANN401 - Slack poster, structurally typed upstream
        reconcile: Any,  # noqa: ANN401 - Slack reconciler, as above
    ) -> str: ...


def record_and_resume(
    *,
    settings: Settings,
    connection: Connection,
    drive: Any,  # noqa: ANN401 - googleapiclient resource
    sheets: Any,  # noqa: ANN401 - googleapiclient resource
    slides: Any,  # noqa: ANN401 - googleapiclient resource
    arguments: dict[str, Any],
    thread_ts: str,
    action_id: str,
    progress: Callable[[str], None],
    post_once: Any,  # noqa: ANN401 - Slack poster, structurally typed upstream
    reconcile: Any,  # noqa: ANN401 - Slack reconciler, as above
    may_rebuild: Callable[[Connection, store.RunRow, str, str], bool],
    resume: ResumesRun,
) -> str:
    """Record one `supply_listing_value` reply and continue its paused run.

    Args:
        settings: The frozen configuration.
        connection: This action's own open database connection.
        drive: Drive client, for the roster refresh.
        sheets: Sheets client, for re-reading the exact source row.
        slides: Slides client, for the rebuild.
        arguments: The tool call's arguments as the model returned them.
        thread_ts: The Gable-owned thread the reply arrived in.
        action_id: This reply's stable identity, so a redelivered Slack message
            cannot rebuild the same flyer twice.
        progress: Shows the native waiting state.
        post_once: Durable outbox poster.
        reconcile: Confirms a delivery whose acknowledgement was lost.
        may_rebuild: Claims the run for a rebuild, reopening a delivered one.
        resume: Continues the run from its current sources.

    Returns:
        What to say in the thread. Empty means the resume already spoke for
        itself, which is how a rebuild reports its own outcome.

    Raises:
        Nothing. Every failure becomes a sentence and leaves the run paused.
    """
    run = store.run_for_thread(connection, thread_ts)
    if run is None:
        return NO_LISTING
    try:
        submission = refresh_submission_sources(
            connection,
            run,
            SheetClient(spreadsheet_id=settings.sheet_id, service=sheets),
            drive,
            settings.drive_id,
            settings.drive_templates_folder_id,
        )
    except SourceRefreshError:
        logger.exception("a listing's current form or contact source could not be read")
        return NO_SOURCES
    # The address comes from the freshly re-read row rather than a cached copy,
    # so a stated fact is filed against the property the run is actually about.
    # One reply answers the one batched question, so it routinely carries
    # several values; every one is recorded before the run continues.
    if not record_stated(connection, submission.intake.address, arguments, run.response_row_id):
        return NOT_UNDERSTOOD
    # A stated address changes which property this is, and it is stored over the
    # submission rather than beside it, so the run has to continue from the
    # reloaded row. Reloading always is cheaper than deciding whether an address
    # was among the values.
    reloaded = store.load_submission(connection, run.response_row_id)
    if reloaded is not None:
        submission = reloaded
    # The value is recorded by this point; reopening a finished flyer is what
    # lets the reply actually be used.
    if not may_rebuild(connection, run, action_id, thread_ts):
        return ""
    return resume(
        settings=settings,
        connection=connection,
        drive=drive,
        slides=slides,
        stored=submission,
        run=run,
        thread_ts=thread_ts,
        progress=progress,
        post_once=post_once,
        reconcile=reconcile,
    )
