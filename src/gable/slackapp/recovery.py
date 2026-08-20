"""Startup and process-lifetime recovery for work a restart interrupted.

Gable cannot commit to Slack and SQLite together. Every recovery here therefore
reads durable state rather than process memory: an interrupted run, or an upload
this process accepted but never finished. Each one either resumes idempotently
or is released with a truthful notice, so no listing waits forever on work that
silently died.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from sqlite3 import Connection

from gable.db import store
from gable.pipeline.questions import PostOnce, ReconcilePost, deliver_pending
from gable.slackapp.photos import PHOTO_INGRESS_ROUTE
from gable.voice import safe

logger = logging.getLogger("gable.slack.recovery")


def notify_interrupted_runs(
    connection: Connection,
    interrupted: tuple[store.RunRow, ...],
    say: Callable[[str, str | None], str],
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> int:
    """Persist and attempt durable startup-recovery outcomes.

    Runs with an owned thread were paused for review and receive the notice in
    that thread. Runs interrupted before a root existed are failed and receive
    one channel notice. A failed or unconfirmed Slack call leaves the stable-id
    outbox row pending for the process-lifetime retry loop; it does not wait for
    another startup or consume another flyer attempt.

    Returns:
        Number of notices Slack confirmed and the database acknowledged.

    Raises:
        Nothing. Startup must continue even while Slack is temporarily down.
    """
    enqueue_interrupted_runs(connection, interrupted)
    run_ids = {run.run_id for run in interrupted}
    pending = tuple(
        item for item in store.pending_run_questions(connection) if item.run_id in run_ids
    )
    return notify_pending_run_questions(
        connection,
        pending,
        say,
        post_once,
        reconcile,
    )


def enqueue_interrupted_runs(
    connection: Connection,
    interrupted: tuple[store.RunRow, ...],
) -> None:
    """Move legacy interruption markers into the shared stable-id outbox."""
    for run in interrupted:
        try:
            stored = store.load_submission(connection, run.response_row_id)
            address = stored.intake.address.strip() if stored is not None else ""
            listing = address or "This listing"
            if run.slack_thread_ts:
                message = safe(
                    f"{listing} — I was interrupted while building this flyer, so I "
                    "paused it for review instead of saying it was finished. Tell me to "
                    "run it again."
                )
                thread_ts: str | None = run.slack_thread_ts
            else:
                message = safe(
                    f"{listing} — I was interrupted while building this flyer, and there "
                    "was no listing thread I could safely resume. I marked that attempt "
                    "failed so Chase can check it before retrying."
                )
                thread_ts = None
            store.prepare_run_outcome(
                connection,
                run.run_id,
                run.status,
                message,
                pending_status=run.status,
                thread_ts=thread_ts or "",
                confirmed_reason=store.INTERRUPTED_REASON,
                confirmation_detail="startup interruption reported in Slack",
                transition_detail="startup interruption notice persisted before delivery",
                output_file_id=run.output_file_id,
                output_url=run.output_url,
            )
        except Exception:
            logger.exception("could not persist an interrupted-run notice")


def notify_pending_run_questions(
    connection: Connection,
    pending: tuple[store.PendingRunQuestion, ...],
    say: Callable[[str, str | None], str],
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> int:
    """Retry exact persisted questions and outcomes without rebuilding work.

    Stable client message ids are part of each outbox row. If the process died
    after Slack accepted a post but before SQLite recorded its timestamp, the
    retry carries the same id rather than creating another visible question.
    """
    confirmed = 0
    for item in pending:
        delivered = deliver_pending(
            connection,
            item,
            say,
            post_once=post_once,
            reconcile=reconcile,
        )
        if delivered.confirmed:
            confirmed += 1
    return confirmed


def recover_abandoned_photo_uploads(connection: Connection) -> tuple[str, ...]:
    """Ask again for any upload accepted here but never finished.

    The photo handler claims a durable ingress row before refreshing sources,
    downloading, and publishing, and completes it only once an outcome is
    stored. An incomplete row therefore means the process died mid-preparation.
    Slack does not redeliver that file, so the honest recovery is to say the
    image did not arrive and request it once more in the same thread.

    Args:
        connection: An open connection owned by the calling worker.

    Returns:
        The run ids for which a fresh request was persisted.

    Raises:
        Nothing. Recovery must never stop the worker that runs it.
    """
    recovered: list[str] = []
    for claim in store.abandoned_slack_events(connection, PHOTO_INGRESS_ROUTE):
        try:
            run = store.run_by_id(connection, claim.subject_id)
            if run is None:
                _release(connection, claim, "the listing run no longer exists")
            elif run.photo_event_id == claim.event_id:
                # The claim completed its work; only the release was lost.
                _release(connection, claim, "the run had already recorded this upload")
            elif not (run.status == "needs_photo" or (run.is_paused and run.awaiting_photo)):
                # Same rule as the ingress that accepted the upload. Pinned to
                # `needs_photo`, this released a photo accepted in any other
                # paused state and said nothing: Carmen's image was gone, the
                # thread was silent, and she waited on a flyer that would never
                # come. The release detail also contradicted the run's own
                # column, which is how a silent loss reads in the audit trail.
                _release(connection, claim, "the listing is no longer waiting for a photo")
            elif store.has_pending_run_notification(connection, run.run_id):
                # Something exact is already owed in this thread. A second
                # request would contradict whatever that message says.
                _release(connection, claim, "another exact message already owes this thread")
            else:
                store.prepare_run_question(
                    connection,
                    run.run_id,
                    # Back to the state the ask left it in. A run owed a widened
                    # design as well must not be moved to needs_photo, or the
                    # reply saying the design is fixed stops routing.
                    run.status if run.status in store.PAUSED else "needs_photo",
                    safe(
                        "I was interrupted while preparing the image you sent, so it "
                        "never reached this flyer. Please send it once more here."
                    ),
                    thread_ts=claim.thread_ts or run.slack_thread_ts,
                )
                _release(connection, claim, "interrupted upload reported for a fresh request")
                recovered.append(run.run_id)
        except Exception:
            logger.exception("could not recover an interrupted photo upload")
    return tuple(recovered)


def _release(connection: Connection, claim: store.AbandonedSlackEvent, detail: str) -> None:
    """Complete one durable ingress claim after its outcome is decided."""
    store.complete_slack_event(
        connection,
        claim.route,
        claim.event_id,
        claim.subject_id,
        detail,
    )


def prepare_pending_notifications(connection: Connection) -> None:
    """Run every durable recovery the outbox worker owns, before it drains.

    Args:
        connection: A connection owned solely by the calling worker.

    Raises:
        Nothing. A failed recovery must not stop notification delivery.
    """
    try:
        enqueue_interrupted_runs(connection, store.pending_interrupted_runs(connection))
    except Exception:
        logger.exception("could not enqueue interrupted-run notices")
    recovered = recover_abandoned_photo_uploads(connection)
    if recovered:
        logger.warning("re-requested %d interrupted photo upload(s)", len(recovered))
