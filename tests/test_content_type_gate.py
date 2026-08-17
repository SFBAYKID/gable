"""Tests for the build-or-skip gate on the social-media content type.

Carmen settled this on 2026-08-17: only the static posts are graphics, and an
Instagram Reel or Story is video or animation that her team makes by hand. The
values below are the real distribution on the live tab that day — 55 rows say
"Static Instagram/Facebook Post", 31 "Instagram Reel", 16 "Static Instagram
Post", 8 "Instagram Story", and exactly one says "Instagram reel" in lower case.

The tests that matter are the two failure directions. Skipping something Carmen
wanted is silent data loss, because nothing is posted about a skip; building
something she did not want costs her a glance. So an unknown value must build.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import (
    columns_from_header,
    from_row,
    is_known_content_type,
    wants_a_graphic,
)
from gable.pipeline.poller import Poller
from gable.pipeline.schedule import PollSchedule
from gable.sheets import repository as repo
from gable.sheets.client import ReadsRanges

#: `Form Responses 1`, read through the service account on 2026-08-17. The name
#: is split into two columns and there is no trailing `Notes`, so every column
#: from D rightward sits one further right than the older transcription in
#: `test_intake_headers.py`. The content type is index 11 here.
LIVE_HEADER: list[str] = [
    "Timestamp",
    "Email Address",
    "First Name of Agent",
    "Last Name of Agent",
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
]


def _live_row(timestamp: str, address: str, content_type: str) -> list[str]:
    """One row in the live tab's shape."""
    return [
        timestamp,
        "lolo@cornerhouserealty.com",
        "Lolo",
        "Simmons",
        "ack",
        "New Listing",
        "",
        "",
        "",
        "",
        "",
        content_type,
        address,
        "",
        "Just listed",
        "",
        "",
        "",
        "",
        "Seller",
    ]


class FakeSheet:
    """Returns canned rows for the responses tab and an empty roster."""

    def __init__(self, rows: list[list[str]]) -> None:
        """Hold the rows this fake returns."""
        self.rows = rows

    def read(self, a1_range: str) -> list[list[str]]:
        """Return the roster header or the canned response rows."""
        if "Sales" in a1_range:
            return [["Email", "First Name", "Last Name", "Phone"]]
        return self.rows


@pytest.fixture
def db() -> sqlite3.Connection:
    """A migrated, empty database."""
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


# --- the classifier ---------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["Static Instagram/Facebook Post", "Static Instagram Post"],
)
def test_both_live_wordings_for_a_static_post_build(value: str) -> None:
    """71 of 112 live rows are one of these two. They are the same job."""
    assert wants_a_graphic(value) is True
    assert is_known_content_type(value) is True


@pytest.mark.parametrize("value", ["Instagram Reel", "Instagram Story"])
def test_video_and_animation_are_skipped(value: str) -> None:
    """Carmen: "only the static posts are graphics"."""
    assert wants_a_graphic(value) is False
    assert is_known_content_type(value) is True


def test_the_one_lower_case_row_is_skipped_too() -> None:
    """A real row says "Instagram reel". Case-sensitive matching would build it."""
    assert wants_a_graphic("Instagram reel") is False


def test_surrounding_whitespace_does_not_decide_it() -> None:
    assert wants_a_graphic("  Instagram Reel  ") is False


def test_a_blank_content_type_builds() -> None:
    """Blank is not permission to drop the request silently."""
    assert wants_a_graphic("") is True
    assert is_known_content_type("") is False


def test_an_unrecognised_content_type_builds_and_is_reported_unknown() -> None:
    """A new form option must arrive as an extra design, never as silence."""
    assert wants_a_graphic("Instagram Carousel") is True
    assert is_known_content_type("Instagram Carousel") is False


def test_a_type_that_merely_contains_reel_is_not_skipped() -> None:
    """Matching is exact. Guessing at unseen wording risks dropping real work."""
    assert wants_a_graphic("Static Post and Reel") is True


# --- reading it off the sheet -----------------------------------------------


def test_the_content_type_is_read_by_header_on_the_live_tab() -> None:
    columns = columns_from_header(LIVE_HEADER)
    assert columns["content_type"] == 11
    intake = from_row(
        _live_row("8/1/2026 09:00:00", "1 A St, Baltimore, MD 21201", "Instagram Reel"),
        columns,
    )
    assert intake.content_type == "Instagram Reel"
    assert intake.wants_a_graphic is False
    # The gate must not have cost the tab its other fields.
    assert intake.address == "1 A St, Baltimore, MD 21201"
    assert intake.agent_name == "Lolo Simmons"


def test_an_intake_built_without_a_content_type_still_builds() -> None:
    """Tools and tests construct `Intake` directly; the default must be safe."""
    intake = from_row([], {})
    assert intake.content_type == ""
    assert intake.wants_a_graphic is True


# --- the poller gate --------------------------------------------------------


def test_a_reel_is_never_handed_to_the_runner(db: sqlite3.Connection) -> None:
    sheet = FakeSheet(
        [
            LIVE_HEADER,
            _live_row("8/1/2026 09:00:00", "1 A St, Baltimore, MD 21201", "Instagram Reel"),
            _live_row(
                "8/1/2026 09:01:00",
                "2 B St, Baltimore, MD 21202",
                "Static Instagram/Facebook Post",
            ),
        ]
    )
    repo.adopt_backfill(db, [])
    seen: list[repo.Submission] = []
    started = _poller(db, sheet, seen).one_pass()

    assert started == 1
    assert [s.intake.address for s in seen] == ["2 B St, Baltimore, MD 21202"]


def test_a_skipped_reel_is_recorded_terminally_and_never_reopens(
    db: sqlite3.Connection,
) -> None:
    """Without a terminal row the same Reel is reconsidered every two minutes."""
    sheet = FakeSheet(
        [
            LIVE_HEADER,
            _live_row("8/1/2026 09:00:00", "1 A St, Baltimore, MD 21201", "Instagram Story"),
        ]
    )
    repo.adopt_backfill(db, [])
    seen: list[repo.Submission] = []
    poller = _poller(db, sheet, seen)

    assert poller.one_pass() == 0
    row = db.execute("SELECT status FROM runs").fetchone()
    assert row is not None and row["status"] == "skipped"

    assert poller.one_pass() == 0
    assert seen == []
    assert db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 1


def test_the_skip_reason_names_the_content_type(db: sqlite3.Connection) -> None:
    """A run nobody can explain later is the thing the event log exists for."""
    sheet = FakeSheet(
        [
            LIVE_HEADER,
            _live_row("8/1/2026 09:00:00", "1 A St, Baltimore, MD 21201", "Instagram Reel"),
        ]
    )
    repo.adopt_backfill(db, [])
    _poller(db, sheet, []).one_pass()

    detail = db.execute("SELECT detail FROM run_events WHERE status = 'skipped'").fetchone()[
        "detail"
    ]
    assert "Instagram Reel" in detail


def test_a_skip_that_cannot_be_recorded_is_withheld_rather_than_built(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building it is the one wrong answer. It waits for the next pass instead."""
    sheet = FakeSheet(
        [
            LIVE_HEADER,
            _live_row("8/1/2026 09:00:00", "1 A St, Baltimore, MD 21201", "Instagram Reel"),
        ]
    )
    repo.adopt_backfill(db, [])

    def refuse(_connection: sqlite3.Connection, _response_row_id: str) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "start_run", refuse)
    seen: list[repo.Submission] = []
    assert _poller(db, sheet, seen).one_pass() == 0
    assert seen == []
    assert db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0


def test_an_unknown_content_type_is_still_built(db: sqlite3.Connection) -> None:
    """The safe direction: Carmen sees a design she can ignore."""
    sheet = FakeSheet(
        [
            LIVE_HEADER,
            _live_row("8/1/2026 09:00:00", "1 A St, Baltimore, MD 21201", "Instagram Carousel"),
        ]
    )
    repo.adopt_backfill(db, [])
    seen: list[repo.Submission] = []
    assert _poller(db, sheet, seen).one_pass() == 1
    assert len(seen) == 1


def test_a_pass_of_only_reels_starts_nothing_and_posts_no_batch_summary(
    db: sqlite3.Connection,
) -> None:
    """Chase's instruction was to skip silently; a summary is still a message."""
    sheet = FakeSheet(
        [
            LIVE_HEADER,
            _live_row("8/1/2026 09:00:00", "1 A St, Baltimore, MD 21201", "Instagram Reel"),
            _live_row("8/1/2026 09:01:00", "2 B St, Baltimore, MD 21202", "Instagram Story"),
        ]
    )
    repo.adopt_backfill(db, [])
    batches: list[object] = []
    poller = _poller(db, sheet, [])
    poller.on_batch = batches.append
    assert poller.one_pass() == 0
    assert batches == []
