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
from gable.pipeline.poller import BatchOutcome, Poller
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
        sync_roster=lambda: 0,
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


def test_backfill_adoption_is_atomic_across_a_mid_batch_failure(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sheet = FakeSheet(
        [
            HEADER,
            _row("8/1/2026 09:00:00", "1 A St, Baltimore, MD 21201"),
            _row("8/1/2026 09:01:00", "2 B St, Baltimore, MD 21202"),
        ]
    )
    submissions = repo.read_submissions(sheet, "Form Responses 1")
    original = store.set_status
    calls = 0

    def fail_second_status(
        connection: sqlite3.Connection,
        run_id: str,
        status: str,
        detail: str = "",
        **fields: str | int,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced adoption failure")
        original(connection, run_id, status, detail, **fields)

    monkeypatch.setattr(store, "set_status", fail_second_status)
    with pytest.raises(RuntimeError, match="forced adoption failure"):
        repo.adopt_backfill(db, submissions)

    assert db.execute("SELECT COUNT(*) AS n FROM submissions").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0
    assert not repo.backfill_adopted(db)

    monkeypatch.setattr(store, "set_status", original)
    assert repo.adopt_backfill(db, submissions) == 2
    assert repo.backfill_adopted(db)


def test_a_completed_pass_records_when_status_was_last_checked(db: sqlite3.Connection) -> None:
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    poller = _poller(db, sheet, [])

    before = poller.last_poll_at
    assert before is None
    poller.one_pass()
    completed_at = poller.last_poll_at
    assert completed_at is not None
    assert completed_at.tzinfo is UTC


def test_an_operator_retry_refreshes_the_sheet_then_runs_on_the_main_loop(
    db: sqlite3.Connection,
) -> None:
    """Slack workers queue; the poller-owned thread performs all shared I/O."""
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    retried: list[str] = []
    poller: Poller

    def retry(run_id: str) -> None:
        retried.append(run_id)
        poller.stop()

    poller = Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        sync_roster=lambda: 0,
        on_submission=lambda _submission: None,
        on_retry=retry,
    )
    poller.pause()

    assert poller.queue_retry("run-source") is True
    assert poller.queue_retry("run-source") is False
    assert poller.run_forever() == 0

    assert retried == ["run-source"]
    assert sheet.reads == 1, "the current form row is refreshed before retrying"
    assert poller.last_poll_at is not None


def test_an_operator_recheck_refreshes_sources_then_resumes_the_same_run(
    db: sqlite3.Connection,
) -> None:
    """A human pause re-enters without consuming a fresh run attempt."""
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    resumed: list[str] = []
    poller: Poller

    def resume(run_id: str) -> None:
        resumed.append(run_id)
        poller.stop()

    poller = Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        sync_roster=lambda: 0,
        on_submission=lambda _submission: None,
        on_resume=resume,
    )
    poller.pause()

    assert poller.queue_resume("run-paused") is True
    assert poller.queue_resume("run-paused") is False
    assert poller.run_forever() == 0

    assert resumed == ["run-paused"]
    assert sheet.reads == 1


def test_a_failed_source_refresh_does_not_run_queued_operator_work(
    db: sqlite3.Connection,
) -> None:
    """A retry must not use stale saved fields when the current Sheet is unavailable."""
    repo.adopt_backfill(db, [])
    retried: list[str] = []
    poller: Poller

    class BrokenAndStop:
        """Stop the test loop while simulating one failed current-source read."""

        def read(self, _a1_range: str) -> list[list[str]]:
            """Stop, then fail before any source can be called current."""
            poller.stop()
            raise SheetError("temporary Sheet failure")

    poller = Poller(
        client=BrokenAndStop(),
        connection=db,
        responses_tab="Form Responses 1",
        sync_roster=lambda: 0,
        on_submission=lambda _submission: None,
        on_retry=retried.append,
    )
    poller.pause()

    assert poller.queue_retry("run-source") is True
    assert poller.run_forever() == 0
    assert retried == []
    assert poller._operator_task_count() == 1


def test_operator_work_survives_one_failed_refresh_and_runs_after_the_next(
    db: sqlite3.Connection,
) -> None:
    """A transient source failure defers work rather than losing or immediately running it."""
    repo.adopt_backfill(db, [])
    retried: list[str] = []
    poller: Poller

    class BrokenOnce:
        """Fail the first read, then expose an empty current Sheet."""

        def __init__(self) -> None:
            """Start with no reads."""
            self.reads = 0

        def read(self, _a1_range: str) -> list[list[str]]:
            """Fail once, then return the real header."""
            self.reads += 1
            if self.reads == 1:
                raise SheetError("temporary Sheet failure")
            return [HEADER]

    sheet = BrokenOnce()

    def retry(run_id: str) -> None:
        retried.append(run_id)
        poller.stop()

    poller = Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        sync_roster=lambda: 0,
        on_submission=lambda _submission: None,
        on_retry=retry,
        schedule=PollSchedule(busy_interval_seconds=1, quiet_interval_seconds=1),
    )
    poller.pause()

    assert poller.queue_retry("run-source") is True
    assert poller.run_forever() == 0
    assert sheet.reads == 2
    assert retried == ["run-source"]


def test_resume_requests_an_immediate_catch_up_pass(db: sqlite3.Connection) -> None:
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    poller = _poller(db, sheet, [])
    poller.pause()

    was_paused = poller.is_paused
    assert was_paused is True
    poller.resume()
    is_active = not poller.is_paused
    assert is_active is True
    assert poller._force_pass.is_set()  # the loop will not wait for its old schedule


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
    # Nothing finished it, so it stays pending. Polling still must not start a
    # second worker for the same request.
    poller.one_pass()
    rows = db.execute("SELECT COUNT(*) AS n FROM submissions").fetchone()["n"]
    assert rows == 1


def test_an_edited_form_row_refreshes_the_saved_payload_without_opening_a_run(
    db: sqlite3.Connection,
) -> None:
    """A later explicit retry must use the correction, not the first poll's copy."""
    original = _row("8/2/2026", "2 B Rd, Baltimore, MD 21202")
    sheet = FakeSheet([HEADER, original])
    repo.adopt_backfill(db, [])
    seen: list[repo.Submission] = []
    poller = _poller(db, sheet, seen)

    assert poller.one_pass() == 1
    run = store.start_run(db, seen[0].response_row_id)
    store.set_status(db, run.run_id, "needs_photo", "waiting for a supplied photo")

    corrected = original.copy()
    corrected[13] = "Use the corrected description"
    sheet.rows[1] = corrected

    assert poller.one_pass() == 0, "an edit must not fork a paused run automatically"
    saved = store.load_submission(db, seen[0].response_row_id)
    assert saved is not None
    assert saved.intake.post_details == "Use the corrected description"
    assert saved.content_hash != seen[0].content_hash


def test_corrected_email_and_address_reuse_the_deployed_submission_identity(
    db: sqlite3.Connection,
) -> None:
    """Changing tuple fields must update one listing, never reopen the backfill."""
    original = _row("8/2/2026 09:03:02", "2 Typo Rd, Baltimore, MD 21202")
    sheet = FakeSheet([HEADER, original])
    repo.adopt_backfill(db, [])
    seen: list[repo.Submission] = []
    poller = _poller(db, sheet, seen)

    assert poller.one_pass() == 1
    original_id = seen[0].response_row_id
    run = store.start_run(db, original_id)
    store.set_status(db, run.run_id, "needs_info", "waiting for a correction")

    corrected = original.copy()
    corrected[1] = "corrected@cornerhouserealty.com"
    corrected[11] = "2 Corrected Rd, Baltimore, MD 21202"
    sheet.rows[1] = corrected

    assert poller.one_pass() == 0
    rows = db.execute("SELECT response_row_id FROM submissions").fetchall()
    assert [row["response_row_id"] for row in rows] == [original_id]
    saved = store.load_submission(db, original_id)
    assert saved is not None
    assert saved.intake.agent_email == "corrected@cornerhouserealty.com"
    assert saved.intake.address == "2 Corrected Rd, Baltimore, MD 21202"


def test_manual_entry_reconciles_identity_with_the_same_timestamp_guard(
    db: sqlite3.Connection,
) -> None:
    columns = repo.find_header([HEADER])[1]
    original = repo.submission_from_row(
        _row("8/2/2026 09:03:02", "2 Typo Rd, Baltimore, MD 21202"),
        columns,
        2,
    )
    store.record_submission(
        db,
        original.response_row_id,
        original.sheet_row,
        original.submitted_at,
        original.intake,
        original.content_hash,
    )
    corrected_row = _row("8/2/2026 09:03:02", "2 Corrected Rd, Baltimore, MD 21202")
    corrected_row[1] = "corrected@cornerhouserealty.com"
    corrected = repo.submission_from_row(corrected_row, columns, 2)

    reconciled = repo.reconcile_identity(db, corrected)

    assert reconciled.response_row_id == original.response_row_id


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
        sync_roster=lambda: 0,
        on_submission=pause,
    )
    sheet.rows.append(_row("8/2/2026", "2 B Rd, Baltimore, MD 21202"))

    assert poller.one_pass() == 1
    assert poller.one_pass() == 0
    assert len(handed_off) == 1


def test_a_crashing_submission_is_not_reentered_while_its_attempt_is_active(
    db: sqlite3.Connection,
) -> None:
    """The poll loop cannot overlap workers or spend again on a pending row."""
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
        sync_roster=lambda: 0,
        on_submission=leave_pending,
    )
    sheet.rows.append(_row("8/2/2026", "2 B Rd, Baltimore, MD 21202"))

    assert [poller.one_pass() for _ in range(4)] == [1, 0, 0, 0]
    assert len(attempts) == 1


def test_a_pass_is_capped_so_a_backlog_does_not_arrive_at_once(db: sqlite3.Connection) -> None:
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    for n in range(12):
        sheet.rows.append(_row(f"8/{n + 2}/2026", f"{n} C Ave, Baltimore, MD 2120{n % 10}"))
    seen: list[repo.Submission] = []
    poller = _poller(db, sheet, seen)
    poller.max_per_pass = 5
    assert poller.one_pass() == 5


def test_a_pass_reports_actual_run_states_to_one_batch_callback(
    db: sqlite3.Connection,
) -> None:
    """The aggregate ready count comes from runner outcomes, not rows seen."""
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    sheet.rows.extend(
        [
            _row("8/2/2026", "2 B Rd, Baltimore, MD 21202"),
            _row("8/3/2026", "3 C Rd, Baltimore, MD 21203"),
        ]
    )
    batches: list[tuple[BatchOutcome, ...]] = []

    def finish(submission: repo.Submission) -> str:
        return "delivered" if submission.intake.address.startswith("2") else "needs_photo"

    poller = Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        sync_roster=lambda: 0,
        on_submission=finish,
        on_batch=batches.append,
    )

    assert poller.one_pass() == 2
    assert [[item.status for item in batch] for batch in batches] == [["delivered", "needs_photo"]]


def test_a_sheet_failure_does_not_stop_the_watcher(db: sqlite3.Connection) -> None:
    class Broken:
        """A client whose reads always fail."""

        def read(self, a1_range: str) -> list[list[str]]:
            """Always fail, the way a rate-limited Sheets call does."""
            msg = f"boom reading {a1_range}"
            raise SheetError(msg)

    repo.adopt_backfill(db, [])
    assert _poller(db, Broken(), []).one_pass() == 0


def test_a_template_scan_failure_does_not_block_known_listing_work(
    db: sqlite3.Connection,
) -> None:
    sheet = FakeSheet([HEADER])
    repo.adopt_backfill(db, [])
    seen: list[repo.Submission] = []

    def fail_scan() -> int:
        msg = "temporary Drive read failure"
        raise RuntimeError(msg)

    poller = Poller(
        client=sheet,
        connection=db,
        responses_tab="Form Responses 1",
        sync_roster=lambda: 0,
        on_submission=seen.append,
        scan_templates=fail_scan,
    )
    sheet.rows.append(_row("8/2/2026", "2 B Rd, Baltimore, MD 21202"))

    assert poller.one_pass() == 1
    assert len(seen) == 1


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
        sync_roster=lambda: 0,
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
