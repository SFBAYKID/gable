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
import time
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
    on_submission: Callable[[repo.Submission], None]
    schedule: PollSchedule = field(default_factory=PollSchedule)
    max_per_pass: int = MAX_PER_PASS
    _stopping: bool = False

    def stop(self, _signum: int | None = None, _frame: FrameType | None = None) -> None:
        """Ask the loop to finish the current pass and exit.

        Wired to SIGTERM so `systemctl restart` is graceful rather than a kill
        in the middle of a render.
        """
        logger.info("stop requested; finishing this pass")
        self._stopping = True

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
        try:
            self.sync_roster()
            submissions = repo.read_submissions(self.client, self.responses_tab)
        except SheetError:
            logger.exception("could not read the sheet this pass")
            return 0
        except Exception:
            # A roster that cannot be read must not look like an empty one:
            # every flyer would quietly carry the office number and the design's
            # own face. Skip the pass and say so instead.
            logger.exception("could not refresh the agent roster this pass")
            return 0

        pending = repo.new_submissions(self.connection, submissions)
        if not pending:
            return 0

        if len(pending) > self.max_per_pass:
            logger.warning(
                "%d submissions are pending; starting %d this pass",
                len(pending),
                self.max_per_pass,
            )
        started = 0
        for submission in pending[: self.max_per_pass]:
            try:
                self.on_submission(submission)
                started += 1
            except Exception:
                # One bad listing must never stop the batch (ARCHITECTURE 4.2).
                logger.exception("submission at sheet row %d failed", submission.sheet_row)
        return started

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
            delay = self.schedule.interval_seconds(now())
            # Sleep in short slices so a stop request is honoured promptly
            # rather than after a ten-minute nap.
            slept = 0
            while slept < delay and not self._stopping:
                time.sleep(min(2, delay - slept))
                slept += 2
        logger.info("stopped cleanly")
        return 0
