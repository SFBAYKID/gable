"""The loop that watches the Sheet on the schedule Chase set.

07:00 US Central to 19:00 US Pacific it checks every two minutes; outside those
hours, every ten. `pipeline/schedule.py` owns that decision, and this owns the
loop around it.

**No cron.** A cron entry cannot express "every two minutes during the day and
every ten at night" without two entries that disagree at the boundary, and it
gives a fresh process — and therefore a fresh Slack connection and a cold
cache — every time. A long-running loop under systemd restarts on failure, holds
one Socket Mode connection, and asks the schedule how long to sleep after each
pass. That is simpler and fails better.

**It refuses to run on an unprepared database.** The backfill guard lives in the
repository; this checks it before the first pass and says so rather than quietly
doing nothing, because "quietly doing nothing" and "quietly building 99 flyers"
look identical from outside until it is too late.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from sqlite3 import Connection
from types import FrameType
from typing import Final

from gable.pipeline.schedule import PollSchedule
from gable.sheets import repository as repo
from gable.sheets.client import ReadsRanges, SheetError

logger = logging.getLogger("gable.poller")

#: How many submissions one pass will start. A backfill that slipped through
#: would otherwise arrive all at once.
MAX_PER_PASS: Final[int] = 5
MAX_QUEUED_OPERATOR_TASKS: Final[int] = 25


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """One submission's final state within the current poll cycle."""

    address: str
    status: str


@dataclass
class Poller:
    """Watches the Sheet and hands new submissions to a callback."""

    client: ReadsRanges
    connection: Connection
    responses_tab: str
    #: Refreshes the agent roster before a pass. It lives in a Drive workbook
    #: now, not a sheet tab, so the poller is handed a callable rather than a
    #: tab name and never learns which is which.
    sync_roster: Callable[[], int]
    on_submission: Callable[[repo.Submission], str | None]
    #: Receives only work attempted in this pass, after every listing has had
    #: its own precise thread outcome. It never changes an individual run.
    on_batch: Callable[[tuple[BatchOutcome, ...]], None] = lambda _outcomes: None
    #: Starts one explicit fresh attempt by source run id. Operator requests are
    #: drained on the poller's main thread, never inside a Slack worker.
    on_retry: Callable[[str], None] = lambda _run_id: None
    #: Re-enters one existing human-paused run without consuming a new attempt.
    on_resume: Callable[[str], None] = lambda _run_id: None
    #: Reviews newly added source files. The first call adopts the existing
    #: catalogue silently, so enabling this cannot flood Slack on deployment.
    scan_templates: Callable[[], int] = lambda: 0
    schedule: PollSchedule = field(default_factory=PollSchedule)
    max_per_pass: int = MAX_PER_PASS
    _stopping: bool = False
    _paused: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _force_pass: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _operator_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _operator_queue: deque[tuple[str, str]] = field(default_factory=deque, init=False, repr=False)
    _queued_operator_tasks: set[tuple[str, str]] = field(
        default_factory=set, init=False, repr=False
    )
    _last_poll_at: datetime | None = field(default=None, init=False, repr=False)
    _source_refresh_succeeded: bool = field(default=False, init=False, repr=False)

    def stop(self, _signum: int | None = None, _frame: FrameType | None = None) -> None:
        """Ask the loop to finish the current pass and exit.

        Wired to SIGTERM so `systemctl restart` is graceful rather than a kill
        in the middle of a render.
        """
        logger.info("stop requested; finishing this pass")
        self._stopping = True
        self._wake.set()

    @property
    def is_paused(self) -> bool:
        """Return whether scheduled polling is paused by an operator."""
        return self._paused.is_set()

    @property
    def last_poll_at(self) -> datetime | None:
        """Return when the most recent Sheet pass finished in this process."""
        return self._last_poll_at

    def pause(self) -> None:
        """Stop scheduled passes after any pass already running finishes."""
        self._paused.set()
        self._wake.set()
        logger.info("scheduled polling paused by an operator")

    def resume(self) -> None:
        """Resume scheduled polling and request an immediate catch-up pass."""
        self._paused.clear()
        self.request_pass()
        logger.info("scheduled polling resumed by an operator")

    def request_pass(self) -> None:
        """Wake the main loop for one immediate pass, even while paused."""
        self._force_pass.set()
        self._wake.set()

    def queue_retry(self, run_id: str) -> bool:
        """Queue one bounded fresh-run request on the main poller thread.

        Args:
            run_id: Existing run whose submission should receive a fresh attempt.

        Returns:
            True when newly queued. False for a duplicate or full queue.

        Raises:
            Nothing.
        """
        return self._queue_operator_task("retry", run_id)

    def queue_resume(self, run_id: str) -> bool:
        """Queue one existing paused run for a source-refresh and recheck."""
        return self._queue_operator_task("resume", run_id)

    def _queue_operator_task(self, action: str, run_id: str) -> bool:
        """Add one unique bounded task and force the source refresh before it."""
        task = (action, run_id)
        with self._operator_lock:
            if (
                task in self._queued_operator_tasks
                or len(self._operator_queue) >= MAX_QUEUED_OPERATOR_TASKS
            ):
                return False
            self._operator_queue.append(task)
            self._queued_operator_tasks.add(task)
        # The pass refreshes in-place form corrections and the current template
        # catalogue before either kind of operator-requested work reads them.
        self.request_pass()
        return True

    def ready(self) -> tuple[bool, str]:
        """Whether it is safe to start polling.

        Returns:
            `(ok, reason)`. Not ready means the backfill has never been adopted,
            and the reason says exactly what to run.

        Raises:
            sqlite3.Error: on a query failure.
        """
        if not repo.backfill_adopted(self.connection):
            return False, (
                "the historical rows have not been adopted yet, so I will not poll. "
                "Run the backfill once to mark everything currently on the sheet as "
                "history, then start me again."
            )
        return True, "ready"

    def one_pass(self) -> int:
        """Read the Sheet once and hand off anything new.

        Returns:
            How many submissions were handed to the callback.

        Raises:
            Nothing. A pass that fails is logged and the loop continues — a
            transient Sheets error must not stop the watcher.
        """
        self._source_refresh_succeeded = False
        try:
            return self._one_pass()
        finally:
            self._last_poll_at = datetime.now(UTC)

    def _one_pass(self) -> int:
        """Perform one pass behind the timestamp-recording public boundary."""
        try:
            self.sync_roster()
        except Exception:
            # A roster that cannot be read must not look like an empty one:
            # every flyer would quietly carry the office number and the design's
            # own face. Skip the pass and say so instead.
            logger.exception("could not refresh the agent roster this pass")
            return 0

        try:
            scanned = self.scan_templates()
            if scanned:
                logger.info("reviewed %d newly uploaded template(s)", scanned)
        except Exception:
            # A template-folder read must not stop unrelated, already-known
            # listing designs from being used. The listing preflight is still
            # the hard gate before a copy is created.
            logger.exception("could not scan new templates this pass")

        try:
            submissions = repo.read_submissions(self.client, self.responses_tab)
        except SheetError:
            logger.exception("could not read the sheet this pass")
            return 0
        except Exception:
            logger.exception("could not read the sheet this pass")
            return 0

        try:
            pending = repo.new_submissions(self.connection, submissions)
        except Exception:
            logger.exception("could not reconcile the current sheet rows this pass")
            return 0
        # Operator work may use the stored request only after this pass has
        # refreshed every row successfully. A failed read must not rebuild from
        # stale form data merely because the request was already queued.
        self._source_refresh_succeeded = True
        if not pending:
            return 0

        if len(pending) > self.max_per_pass:
            logger.warning(
                "%d submissions are pending; starting %d this pass",
                len(pending),
                self.max_per_pass,
            )
        started = 0
        outcomes: list[BatchOutcome] = []
        for submission in pending[: self.max_per_pass]:
            try:
                status = self.on_submission(submission)
                started += 1
                outcomes.append(BatchOutcome(submission.intake.address, status or "unknown"))
            except Exception:
                # One bad listing must never stop the batch (ARCHITECTURE 4.2).
                logger.exception("submission at sheet row %d failed", submission.sheet_row)
                outcomes.append(BatchOutcome(submission.intake.address, "failed"))
        if outcomes:
            try:
                self.on_batch(tuple(outcomes))
            except Exception:
                # Individual threads have already received their outcomes. A
                # summary failure cannot turn completed listing work into a
                # failed cycle or cause it to be retried.
                logger.exception("could not post the batch summary")
        return started

    def _drain_operator_tasks(self, limit: int) -> tuple[int, int]:
        """Run a bounded queue snapshot on the thread owning Google clients.

        ``limit`` is captured before the source refresh begins. Work arriving
        during that refresh waits for the next pass, guaranteeing that its pass
        began after the operator asked for it.
        """
        resumed = 0
        handled = 0
        processed = 0
        while not self._stopping and processed < limit:
            with self._operator_lock:
                if not self._operator_queue:
                    return resumed, handled
                action, run_id = self._operator_queue.popleft()
                self._queued_operator_tasks.discard((action, run_id))
            processed += 1
            try:
                if action == "resume":
                    self.on_resume(run_id)
                    resumed += 1
                else:
                    self.on_retry(run_id)
                    handled += 1
            except Exception:
                logger.exception("operator %s for %s failed", action, run_id)
        return resumed, handled

    def _operator_task_waiting(self) -> bool:
        """Return whether queued operator work still needs the main loop."""
        with self._operator_lock:
            return bool(self._operator_queue)

    def _operator_task_count(self) -> int:
        """Return a locked snapshot of how much operator work is queued."""
        with self._operator_lock:
            return len(self._operator_queue)

    def _wait(self, timeout: float | None) -> None:
        """Wait until schedule time or an operator request without losing a wakeup."""
        self._wake.clear()
        if self._stopping or self._force_pass.is_set():
            return
        self._wake.wait(timeout=timeout)

    def run_forever(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
        """Poll until asked to stop.

        Args:
            now: Clock, injectable so the loop is testable.

        Returns:
            An exit code. Non-zero when it refused to start.

        Raises:
            Nothing.
        """
        ok, reason = self.ready()
        if not ok:
            logger.error("%s", reason)
            return 2

        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info("watching the sheet, %s", self.schedule.describe(now()))

        while not self._stopping:
            queued_before_refresh = self._operator_task_count()
            forced = self._force_pass.is_set()
            if forced:
                self._force_pass.clear()
            should_refresh = not self.is_paused or forced or queued_before_refresh > 0
            if should_refresh:
                started = self.one_pass()
                if started:
                    logger.info("started %d submission(s)", started)
            resumed = retried = 0
            if queued_before_refresh and self._source_refresh_succeeded:
                resumed, retried = self._drain_operator_tasks(queued_before_refresh)
            if resumed:
                logger.info("rechecked %d paused run(s)", resumed)
            if retried:
                logger.info("started %d operator retry request(s)", retried)
            if self._force_pass.is_set():
                continue
            # A failed source refresh leaves operator work queued, but waits the
            # normal bounded interval before trying again. Immediate looping
            # here would turn a Sheets outage into a quota-burning retry storm.
            operator_waiting = self._operator_task_waiting()
            timeout = (
                None
                if self.is_paused and not operator_waiting
                else self.schedule.interval_seconds(now())
            )
            self._wait(timeout)
        logger.info("stopped cleanly")
        return 0
