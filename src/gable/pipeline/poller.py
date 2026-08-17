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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from sqlite3 import Connection
from types import FrameType
from typing import Final

from gable.db import store
from gable.listings.intake import is_known_content_type
from gable.pipeline.schedule import PollSchedule
from gable.sheets import repository as repo
from gable.sheets.client import ReadsRanges, SheetError
from gable.sheets.identity import Submission

logger = logging.getLogger("gable.poller")

#: How many submissions one pass will start. A backfill that slipped through
#: would otherwise arrive all at once.
MAX_PER_PASS: Final[int] = 5


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
    #: Reviews newly added source files. The first call adopts the existing
    #: catalogue silently, so enabling this cannot flood Slack on deployment.
    scan_templates: Callable[[], int] = lambda: 0
    schedule: PollSchedule = field(default_factory=PollSchedule)
    max_per_pass: int = MAX_PER_PASS
    _stopping: bool = False
    _wake: threading.Event = field(default_factory=threading.Event, init=False, repr=False)

    def stop(self, _signum: int | None = None, _frame: FrameType | None = None) -> None:
        """Ask the loop to finish the current pass and exit.

        Wired to SIGTERM so `systemctl restart` is graceful rather than a kill
        in the middle of a render.
        """
        logger.info("stop requested; finishing this pass")
        self._stopping = True
        self._wake.set()

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
        return self._one_pass()

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
            pending = repo.new_submissions(
                self.connection,
                submissions,
                source_tab=self.responses_tab,
            )
        except Exception:
            logger.exception("could not reconcile the current sheet rows this pass")
            return 0

        pending = self._drop_animated(pending)
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

    def _drop_animated(self, pending: list[Submission]) -> list[Submission]:
        """Retire the submissions that never wanted a graphic.

        A Reel or a Story is video or animation, which Carmen's team makes by
        hand. Each is recorded terminally so polling never reopens it, and
        nothing is posted: Chase's instruction on 2026-08-17 was to skip them
        silently, and at 40 of 112 live rows a notice per row would be noise.

        Args:
            pending: Submissions this pass would otherwise start.

        Returns:
            Only the submissions that want a graphic, in the order given. One
            whose skip could not be recorded is withheld too and retried next
            pass — building it is the one wrong answer available here.

        Raises:
            Nothing. A skip that cannot be recorded is logged, not raised, so
            one bad row cannot stop the batch (ARCHITECTURE 4.2).
        """
        wanted: list[Submission] = []
        for submission in pending:
            intake = submission.intake
            if intake.wants_a_graphic:
                if not is_known_content_type(intake.content_type):
                    # Not an error — an unrecognised or empty value still
                    # builds. Logged so a new form option surfaces here rather
                    # than as a design nobody expected.
                    logger.info(
                        "sheet row %d has an unrecognised content type %r; building anyway",
                        submission.sheet_row,
                        intake.content_type,
                    )
                wanted.append(submission)
                continue
            if self._record_skip(submission):
                logger.info(
                    "sheet row %d asks for %r, which needs no graphic; skipped",
                    submission.sheet_row,
                    intake.content_type,
                )
        return wanted

    def _record_skip(self, submission: Submission) -> bool:
        """Retire one submission terminally, in a single transaction.

        The insert and the terminal status must land together or not at all. A
        half-written skip leaves a `pending` run, and that is the worst
        available outcome: `has_been_handled` already reports the row done so
        nothing reconsiders it, `pending` is an ACTIVE status so `start_run`
        refuses another attempt, and the next startup's
        `recover_interrupted_runs` fails it and owes Carmen a Slack notice
        about a Reel Gable promised to say nothing about. `adopt_backfill`
        wraps the same pair for the same reason.

        Args:
            submission: The submission to retire.

        Returns:
            True when the skip is durably recorded. False leaves the row
            untouched and pending, so the next pass tries again.

        Raises:
            Nothing. One unrecordable row must not stop the batch.
        """
        try:
            # SAVEPOINT-based, so `start_run` and `set_status` nest inside this
            # caller-owned transaction rather than committing independently.
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                run = store.start_run(self.connection, submission.response_row_id)
                store.set_status(
                    self.connection,
                    run.run_id,
                    "skipped",
                    f"content type {submission.intake.content_type!r} is video or animation",
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        except Exception:
            logger.exception("could not record the skip for sheet row %d", submission.sheet_row)
            return False
        return True

    def _wait(self, timeout: float | None) -> None:
        """Wait until the next scheduled pass or a shutdown request."""
        self._wake.clear()
        if self._stopping:
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
            started = self.one_pass()
            if started:
                logger.info("started %d submission(s)", started)
            self._wait(self.schedule.interval_seconds(now()))
        logger.info("stopped cleanly")
        return 0
