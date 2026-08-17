"""Persist, post, and confirm exact Slack questions and run outcomes.

The runner uses this boundary for every question, review, failure, and final
link. Keeping the protocol out of ``runner.py`` makes its ordering auditable and
gives the process-lifetime worker the same retry without rebuilding a flyer.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from sqlite3 import Connection
from typing import Final

from gable.db import store
from gable.db.schema import connect

logger = logging.getLogger("gable.questions")

Post = Callable[[str, str | None], str]
PostOnce = Callable[[str, str | None, str], str]
PreparePending = Callable[[Connection], None]
DrainAdditional = Callable[[Connection], int]
ClaimDelivery = Callable[[int, str], str]
ReleaseDelivery = Callable[[str], None]

_PHOTO_REPLACEMENT_FACT_LIMIT: Final[int] = 480
_DELIVERY_LOCK_STRIPES: Final[int] = 64
_DELIVERY_LOCKS: Final[tuple[threading.RLock, ...]] = tuple(
    threading.RLock() for _ in range(_DELIVERY_LOCK_STRIPES)
)
QUESTION_RETRY_INTERVAL_SECONDS: Final[float] = 60.0
# Slack WebClient's installed default HTTP timeout is 30 seconds. A stale claim
# cannot fence an external request that is still in flight, so reclaim only
# after four full timeout windows plus the complete-history absence proof.
DELIVERY_RETRY_GRACE_SECONDS: Final[float] = 120.0


class ReconcileState(Enum):
    """What one complete Slack-history proof established."""

    FOUND = "found"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """Tri-state history result; empty text never conflates absence with failure."""

    state: ReconcileState
    timestamp: str = ""


ReconcilePost = Callable[[str, str | None, str, str], Reconciliation]


@dataclass(frozen=True, slots=True)
class Delivery:
    """What one outbox-delivery attempt can truthfully report to its caller."""

    status: str
    confirmed: bool = False
    said: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()


@contextmanager
def notification_guard(notification_key: str) -> Iterator[None]:
    """Serialize one notification's external write inside this process."""
    lock = _DELIVERY_LOCKS[hash(notification_key) % _DELIVERY_LOCK_STRIPES]
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def run_question_guard(run_id: str) -> Iterator[None]:
    """Serialize one run's external post against an answering photo upload.

    SQLite compare-and-swap remains the durable authority.  This process-local
    guard closes the external-call gap: once a photo has atomically satisfied a
    pending question, a delivery worker that held an older row cannot post it.
    ``RLock`` is required because a resumed photo run can discover that the
    replacement also conflicts and prepare its next question on the same stack.
    """
    with notification_guard(f"run:{run_id}"):
        yield


def prepare_and_deliver(
    connection: Connection,
    run_id: str,
    message: str,
    target_status: str,
    say: Post,
    *,
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
    question_label: str = "",
    headline: str = "",
    thread_ts: str = "",
    confirmed_reason: str = "",
    confirmation_detail: str = "",
    transition_detail: str = "Slack question persisted before delivery",
    output_file_id: str = "",
    output_url: str = "",
) -> Delivery:
    """Write one question to the outbox, then attempt its Slack delivery."""
    pending = store.prepare_run_question(
        connection,
        run_id,
        target_status,
        message,
        question_label=question_label,
        headline=headline,
        thread_ts=thread_ts,
        confirmed_reason=confirmed_reason,
        confirmation_detail=confirmation_detail,
        transition_detail=transition_detail,
        output_file_id=output_file_id,
        output_url=output_url,
    )
    return deliver_pending(
        connection,
        pending,
        say,
        post_once=post_once,
        reconcile=reconcile,
    )


def prepare_outcome_and_deliver(
    connection: Connection,
    run_id: str,
    message: str,
    target_status: str,
    say: Post,
    *,
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
    pending_status: str | None = None,
    thread_ts: str = "",
    confirmed_reason: str = "",
    confirmation_detail: str = "Slack confirmed the run outcome",
    transition_detail: str = "Slack outcome persisted before delivery",
    output_file_id: str = "",
    output_url: str = "",
) -> Delivery:
    """Write one exact review or terminal outcome, then attempt delivery."""
    pending = store.prepare_run_outcome(
        connection,
        run_id,
        target_status,
        message,
        pending_status=pending_status,
        thread_ts=thread_ts,
        confirmed_reason=confirmed_reason,
        confirmation_detail=confirmation_detail,
        transition_detail=transition_detail,
        output_file_id=output_file_id,
        output_url=output_url,
    )
    return deliver_pending(
        connection,
        pending,
        say,
        post_once=post_once,
        reconcile=reconcile,
    )


def deliver_pending(
    connection: Connection,
    pending: store.PendingRunQuestion,
    say: Post,
    *,
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> Delivery:
    """Deliver only while the persisted item is still unresolved."""
    with run_question_guard(pending.run_id):
        current = store.pending_run_question(connection, pending.question_id)
        if current is None:
            run = store.run_by_id(connection, pending.run_id)
            return Delivery(status=run.status if run is not None else "needs_review")
        return _deliver_pending_locked(
            connection,
            current,
            say,
            post_once=post_once,
            reconcile=reconcile,
        )


def post_persisted_notification(
    text: str,
    thread_ts: str | None,
    client_id: str,
    created_at: str,
    attempted_at: str,
    attempt_count: int,
    say: Post,
    *,
    claim: ClaimDelivery,
    release: ReleaseDelivery,
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> str:
    """Return Slack's timestamp after one conservative outbox delivery pass.

    History is checked before the write so a prior pass whose acknowledgement
    was lost cannot write again before proving whether Slack accepted it. One
    pass makes at most one external write. A missing acknowledgement is checked
    immediately, then left pending for the process-lifetime worker rather than
    creating a tight retry loop.
    """

    def recover() -> Reconciliation:
        if reconcile is None:
            return Reconciliation(ReconcileState.UNKNOWN)
        try:
            return reconcile(text, thread_ts, created_at, client_id)
        except Exception:
            # Production's Slack adapter already fails closed. Keep this
            # boundary because an audit read must never turn a durable item
            # into a terminal pipeline exception.
            logger.exception("could not reconcile an unconfirmed Slack post")
            return Reconciliation(ReconcileState.UNKNOWN)

    recovered = recover()
    if recovered.state is ReconcileState.FOUND and recovered.timestamp.strip():
        return recovered.timestamp.strip()
    stale_before = datetime.now(UTC) - timedelta(seconds=DELIVERY_RETRY_GRACE_SECONDS)
    if attempt_count:
        if recovered.state is not ReconcileState.ABSENT:
            return ""
        try:
            attempted = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
            if attempted.tzinfo is None:
                attempted = attempted.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return ""
        if attempted > stale_before:
            return ""
    claim_token = claim(attempt_count, stale_before.isoformat())
    if not claim_token:
        return ""
    try:
        try:
            posted_ts = (
                post_once(text, thread_ts, client_id)
                if post_once is not None
                else say(text, thread_ts)
            )
        except Exception:
            logger.exception("Slack outbox delivery attempt failed")
        else:
            if posted_ts.strip():
                return posted_ts.strip()
            logger.error("Slack did not confirm the outbox delivery attempt")
    finally:
        release(claim_token)
    recovered = recover()
    if recovered.state is ReconcileState.FOUND:
        return recovered.timestamp.strip()
    return ""


def _inside_its_run_thread(
    connection: Connection,
    pending: store.PendingRunQuestion,
) -> store.PendingRunQuestion:
    """Give a thread-less notification the thread its own run already opened.

    A run announces itself once and everything it says afterwards belongs under
    that announcement. An outcome persisted without a thread contradicts that:
    the delivery path below reads no thread and no headline as "this is the
    first thing said about this work", so it tries to post the outcome as a new
    root message in the channel and then bind it as the run's thread. For a run
    that has already announced itself the bind is refused — the run holds a
    different thread — and the row can never resolve. One real Sold review sat
    that way from 2026-08-14 until it was found the next day.

    Refusing was right. A bare "I rendered it, but..." posted at channel level,
    detached from the listing it belongs to, is worse than a stuck row. The
    error was reaching that branch at all.

    Args:
        connection: The open database.
        pending: The notification about to be delivered.

    Returns:
        The same notification, carrying its run's thread when it had none and
        the run has one. Unchanged in every other case, including a run that
        genuinely has no thread yet.

    Raises:
        Nothing.
    """
    if pending.thread_ts.strip() or pending.headline.strip():
        return pending
    run = store.run_by_id(connection, pending.run_id)
    root = (run.slack_thread_ts or "").strip() if run is not None else ""
    if not root:
        return pending
    if not store.adopt_run_thread_for_notification(connection, pending.question_id, root):
        return pending
    logger.info("delivering a thread-less notification under its run's own thread")
    return replace(pending, thread_ts=root)


def _deliver_pending_locked(
    connection: Connection,
    pending: store.PendingRunQuestion,
    say: Post,
    *,
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> Delivery:
    """Attempt one persisted question without rerunning any flyer work."""

    def post(text: str, thread_ts: str | None, client_id: str, phase: str) -> str:
        attempt_count = (
            current.headline_attempt_count
            if phase == "headline"
            else current.question_attempt_count
        )
        attempted_at = (
            current.headline_attempted_at if phase == "headline" else current.question_attempted_at
        )
        return post_persisted_notification(
            text,
            thread_ts,
            client_id,
            current.created_at,
            attempted_at,
            attempt_count,
            say,
            claim=lambda expected_count, stale_before: store.claim_run_notification_delivery(
                connection,
                current.question_id,
                phase,
                expected_count,
                stale_before,
            ),
            release=lambda token: store.release_run_notification_delivery(
                connection,
                current.question_id,
                token,
            ),
            post_once=post_once,
            reconcile=reconcile,
        )

    current = pending
    said: list[str] = []
    try:
        current = _inside_its_run_thread(connection, current)

        if current.headline and not current.thread_ts:
            headline_ts = post(
                current.headline,
                None,
                current.headline_client_id,
                "headline",
            ).strip()
            if not headline_ts:
                logger.error("Slack did not confirm a listing announcement")
                return Delivery(status=current.pending_status)
            if not store.bind_run_question_thread(
                connection,
                current.question_id,
                headline_ts,
            ):
                logger.error("the pending question could not claim its listing thread")
                run = store.run_by_id(connection, current.run_id)
                return Delivery(status=run.status if run is not None else "needs_review")
            rebound = store.pending_run_question(connection, current.question_id)
            if rebound is None:
                run = store.run_by_id(connection, current.run_id)
                return Delivery(status=run.status if run is not None else "needs_review")
            current = rebound
            said.append(current.headline)

        if not current.headline and not current.thread_ts:
            # A question arising from work with no prior Slack root becomes
            # the root itself. This includes a bad supplied URL in a local run;
            # inventing an empty parent would make the pause unreachable.
            root_ts = post(
                current.message,
                None,
                current.question_client_id,
                "question",
            ).strip()
            if not root_ts:
                logger.error("Slack did not confirm a root run notification")
                return Delivery(status=current.pending_status)
            if not store.bind_run_question_thread(connection, current.question_id, root_ts):
                logger.error("the root question could not claim its listing thread")
                run = store.run_by_id(connection, current.run_id)
                return Delivery(status=run.status if run is not None else "needs_review")
            current = replace(current, thread_ts=root_ts, headline_ts=root_ts)
            said.append(current.message)
            if not store.confirm_run_question(connection, current.question_id, root_ts):
                logger.error("the root Slack question was no longer pending")
                run = store.run_by_id(connection, current.run_id)
                return Delivery(
                    status=run.status if run is not None else "needs_review",
                    said=tuple(said),
                )
            return Delivery(
                status=current.target_status,
                confirmed=True,
                said=tuple(said),
                questions=(current.question_label,)
                if current.notification_kind == "question"
                else (),
            )

        if not current.thread_ts:
            logger.error("a pending Slack notification has no confirmed thread")
            return Delivery(status=current.pending_status, said=tuple(said))

        # If a no-headline question already became the root before a crash, its
        # persisted headline timestamp is also its confirmation timestamp. Do
        # not post it once more as a reply to itself during startup recovery.
        notification_ts = current.headline_ts if not current.headline else ""
        if not notification_ts:
            notification_ts = post(
                current.message,
                current.thread_ts,
                current.question_client_id,
                "question",
            ).strip()
        if not notification_ts:
            logger.error("Slack did not confirm a run notification")
            return Delivery(status=current.pending_status, said=tuple(said))
        if current.message not in said:
            said.append(current.message)
        if not store.confirm_run_question(connection, current.question_id, notification_ts):
            logger.error("the Slack question was no longer pending")
            run = store.run_by_id(connection, current.run_id)
            return Delivery(
                status=run.status if run is not None else "needs_review",
                said=tuple(said),
            )
        return Delivery(
            status=current.target_status,
            confirmed=True,
            said=tuple(said),
            questions=(current.question_label,) if current.notification_kind == "question" else (),
        )
    except Exception:
        logger.exception("could not deliver a persisted Slack notification")
        return Delivery(status=current.pending_status, said=tuple(said))


def drain_pending(
    connection: Connection,
    say: Post,
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> int:
    """Attempt every currently actionable question once and count confirmations."""
    confirmed = 0
    for item in store.pending_run_questions(connection):
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


class RunNotificationRetryLoop:
    """Retry every durable Slack notification independently of Sheet polling.

    Every pass owns a fresh SQLite connection.  The Socket listener, Sheet
    poller, and retry worker therefore never concurrently share a connection
    object, including in the controlled release mode where Sheet polling is off.
    """

    def __init__(
        self,
        db_path: Path,
        say: Post,
        post_once: PostOnce,
        *,
        reconcile: ReconcilePost | None = None,
        interval_seconds: float = QUESTION_RETRY_INTERVAL_SECONDS,
        prepare_pending: PreparePending = lambda _connection: None,
        drain_additional: DrainAdditional = lambda _connection: 0,
    ) -> None:
        """Bind the durable database and Slack posting seams."""
        if interval_seconds <= 0:
            raise ValueError("notification retry interval must be positive")
        self.db_path = db_path
        self.say = say
        self.post_once = post_once
        self.reconcile = reconcile
        self.prepare_pending = prepare_pending
        self.drain_additional = drain_additional
        self.interval_seconds = interval_seconds
        self._stopping = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start one immediate-then-periodic delivery worker."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="gable-notification-outbox",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        """Wake and join the worker before Slack or process resources close."""
        with self._lifecycle_lock:
            worker = self._thread
            self._stopping.set()
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        with self._lifecycle_lock:
            if self._thread is worker:
                self._thread = None

    def drain_once(self) -> int:
        """Drain through a connection owned only by the calling worker."""
        connection = connect(self.db_path)
        try:
            self.prepare_pending(connection)
            pending_count = len(store.pending_run_questions(connection))
            confirmed = drain_pending(
                connection,
                self.say,
                self.post_once,
                self.reconcile,
            )
            if confirmed != pending_count:
                logger.warning(
                    "%d Slack notification(s) remain pending",
                    pending_count - confirmed,
                )
            return confirmed + self.drain_additional(connection)
        finally:
            connection.close()

    def _run(self) -> None:
        """Retry until lifecycle shutdown, isolating one failed pass."""
        while not self._stopping.is_set():
            try:
                confirmed = self.drain_once()
                if confirmed:
                    logger.info("delivered %d pending Slack notification(s)", confirmed)
            except Exception:
                logger.exception("could not retry pending Slack notifications")
            self._stopping.wait(self.interval_seconds)


# Compatibility for focused callers written while this outbox carried only questions.
RunQuestionRetryLoop = RunNotificationRetryLoop
