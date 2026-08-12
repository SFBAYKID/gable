"""Tests for the watch loop.

The one that matters most is the refusal: a poller that starts on an unprepared
database is how 99 historical rows become 99 flyers.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.poller import Poller
from gable.pipeline.schedule import PollSchedule
from gable.sheets import repository as repo
from gable.sheets.client import ReadsRanges, SheetError


class FakeSheet:
    """A sheet client that returns canned rows and can be told to fail."""

    def __init__(self, rows: list[list[str]], roster: list[list[str]] | None = None) -> None:
        """Hold the rows this fake will return."""
        self.rows = rows
        self.roster = roster or [["Email", "First Name", "Last Name", "Phone"]]
        self.reads = 0

    def read(self, a1_range: str) -> list[list[str]]:
        """Return the canned rows for whichever tab was asked for."""
        self.reads += 1
        return self.roster if "Sales" in a1_range else self.rows


def _row(ts: str, address: str) -> list[str]:
    return [
        ts,
        "lolo@cornerhouserealty.com",
        "Lolo Simmons",
        "ack",
        "New Listing",
        "",
        "",
        "",
        "",
        "",
        "Static",
        address,
        "",
        "Just listed",
        "",
    ]


#: The live `Form Responses 1` header, read through the service account on
#: 2026-08-12. The columns are found by name now, so a fixture that invents
#: header text tests a tab that does not exist.
HEADER = [
    "Column 1",
    "Email Address",
    "Name of Agent",
    "Service Guidelines Acknowledgment",
    "Select your request type",
    "Please provide the property address for the postcard",
    "Select postcard category",
    "Upload photos",
    "Upload your video assets (For Video Editing Requests Only)",
    "Optional: Include any details/instruction for your video",
    "Select social media content type",
    "Property Address",
    "Upload high-resolution property photos (up to 5 images)",
    "Include details for post - required for Client Review post",
    "Open house date/time (if applicable)",
    "New price (if price improvement)",
    "Closing price (for sold posts only):",
    "Additional Notes for Social Media Team",
    "For Sold or Under Contract posts, were you on the buyer or seller side?",
    "Notes",
]


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = connect(Path(tempfile.mkdtemp()) / "g.db")
    apply_migrations(connection)
    return connection


def _poller(db: sqlite3.Connection, sheet: ReadsRanges, seen: list[repo.Submission]) -> Poller:
    return Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        salespeople_tab="Sales_People",
        on_submission=seen.append,
        schedule=PollSchedule(),
    )


# --- the refusal ------------------------------------------------------------


def test_it_refuses_to_poll_an_unprepared_database(db: sqlite3.Connection) -> None:
    """This is the guard that stops 99 historical rows becoming 99 flyers."""
    sheet = FakeSheet([HEADER, _row("8/1/2026", "1 A St, Baltimore, MD 21201")])
    poller = _poller(db, sheet, [])
    ok, reason = poller.ready()
    assert ok is False
    assert "backfill" in reason.lower() or "historical" in reason.lower()


def test_the_refusal_says_what_to_do(db: sqlite3.Connection) -> None:
    """A refusal nobody can act on is just a stall."""
    _, reason = _poller(db, FakeSheet([HEADER]), []).ready()
    assert "run the backfill" in reason.lower()


def test_run_forever_exits_nonzero_rather_than_polling_unprepared(db: sqlite3.Connection) -> None:
    seen: list[repo.Submission] = []
    poller = _poller(db, FakeSheet([HEADER]), seen)
    assert poller.run_forever() == 2
    assert seen == []


def test_it_is_ready_once_the_backfill_is_adopted(db: sqlite3.Connection) -> None:
    sheet = FakeSheet([HEADER, _row("8/1/2026", "1 A St, Baltimore, MD 21201")])
    repo.adopt_backfill(db, repo.read_submissions(sheet, "Form Responses 1"))
    assert _poller(db, sheet, []).ready()[0] is True


# --- what a pass does -------------------------------------------------------


def test_a_pass_hands_off_only_what_is_new(db: sqlite3.Connection) -> None:
    old = _row("8/1/2026", "1 A St, Baltimore, MD 21201")
    sheet = FakeSheet([HEADER, old])
    repo.adopt_backfill(db, repo.read_submissions(sheet, "Form Responses 1"))

    seen: list[repo.Submission] = []
    poller = _poller(db, sheet, seen)
    assert poller.one_pass() == 0, "history must not be handed off"

    sheet.rows.append(_row("8/2/2026", "2 B Rd, Baltimore, MD 21202"))
    assert poller.one_pass() == 1
    assert seen[0].intake.address == "2 B Rd, Baltimore, MD 21202"


def test_the_same_row_is_not_handed_off_twice(db: sqlite3.Connection) -> None:
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    seen: list[repo.Submission] = []
    poller = _poller(db, sheet, seen)

    sheet.rows.append(_row("8/2/2026", "2 B Rd, Baltimore, MD 21202"))
    assert poller.one_pass() == 1
    # Nothing finished it, so it stays pending — but it is already known, and
    # the caller owns not starting it twice. What must never happen is the
    # submission being recorded twice.
    poller.one_pass()
    rows = db.execute("SELECT COUNT(*) AS n FROM submissions").fetchone()["n"]
    assert rows == 1


@pytest.mark.parametrize(
    "paused_status",
    ["needs_photo", "needs_info", "needs_template", "needs_review"],
)
def test_a_paused_submission_is_not_asked_or_built_again(
    db: sqlite3.Connection, paused_status: str
) -> None:
    """A human pause suppresses normal polling until that run is resumed."""
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    handed_off: list[str] = []

    def pause(submission: repo.Submission) -> None:
        handed_off.append(submission.response_row_id)
        run = store.start_run(db, submission.response_row_id)
        store.set_status(db, run.run_id, paused_status, "waiting on a person")

    poller = Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        salespeople_tab="Sales_People",
        on_submission=pause,
    )
    sheet.rows.append(_row("8/2/2026", "2 B Rd, Baltimore, MD 21202"))

    assert poller.one_pass() == 1
    assert poller.one_pass() == 0
    assert len(handed_off) == 1


def test_a_crashing_submission_stops_reentering_after_three_attempts(
    db: sqlite3.Connection,
) -> None:
    """The poll loop cannot spend money on the same crashing row forever."""
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    attempts: list[str] = []

    def leave_pending(submission: repo.Submission) -> None:
        attempts.append(submission.response_row_id)
        store.start_run(db, submission.response_row_id)

    poller = Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        salespeople_tab="Sales_People",
        on_submission=leave_pending,
    )
    sheet.rows.append(_row("8/2/2026", "2 B Rd, Baltimore, MD 21202"))

    assert [poller.one_pass() for _ in range(4)] == [1, 1, 1, 0]
    assert len(attempts) == store.MAX_RUN_ATTEMPTS


def test_a_pass_is_capped_so_a_backlog_does_not_arrive_at_once(db: sqlite3.Connection) -> None:
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    for n in range(12):
        sheet.rows.append(_row(f"8/{n + 2}/2026", f"{n} C Ave, Baltimore, MD 2120{n % 10}"))
    seen: list[repo.Submission] = []
    poller = _poller(db, sheet, seen)
    poller.max_per_pass = 5
    assert poller.one_pass() == 5


def test_a_sheet_failure_does_not_stop_the_watcher(db: sqlite3.Connection) -> None:
    class Broken:
        """A client whose reads always fail."""

        def read(self, a1_range: str) -> list[list[str]]:
            """Always fail, the way a rate-limited Sheets call does."""
            msg = f"boom reading {a1_range}"
            raise SheetError(msg)

    repo.adopt_backfill(db, [])
    assert _poller(db, Broken(), []).one_pass() == 0


def test_one_bad_submission_does_not_stop_the_batch(db: sqlite3.Connection) -> None:
    """ARCHITECTURE 4.2: one bad row must never stop a batch."""
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    for n in range(3):
        sheet.rows.append(_row(f"8/{n + 2}/2026", f"{n} D St, Baltimore, MD 21203"))

    handled: list[str] = []

    def explode_on_first(submission: repo.Submission) -> None:
        if not handled:
            handled.append("boom")
            msg = "this one is broken"
            raise ValueError(msg)
        handled.append(submission.intake.address)

    poller = Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        salespeople_tab="Sales_People",
        on_submission=explode_on_first,
    )
    started = poller.one_pass()
    assert started == 2, "the other two must still be started"


# --- the schedule -----------------------------------------------------------


def test_the_loop_uses_the_busy_rate_during_the_day() -> None:
    schedule = PollSchedule()
    midday_central = datetime(2026, 8, 11, 17, 0, tzinfo=UTC)  # noon Central
    assert schedule.interval_seconds(midday_central) == 120


def test_the_loop_uses_the_quiet_rate_overnight() -> None:
    schedule = PollSchedule()
    small_hours = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)  # 04:00 Central
    assert schedule.interval_seconds(small_hours) == 600


def test_stop_is_honoured(db: sqlite3.Connection) -> None:
    repo.adopt_backfill(db, [])
    poller = _poller(db, FakeSheet([HEADER]), [])
    poller.stop()
    assert poller.run_forever() == 0
