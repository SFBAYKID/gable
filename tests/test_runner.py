"""Tests for the one module that performs a run.

Every outside call is injected, so the whole sequence is exercised without
Google, Slack or a paid call. The properties under test are the ones that make
it safe to run unattended: every exit records a status, and nothing is guessed.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from gable.db.schema import apply_migrations, connect
from gable.listings.enrich import Facts
from gable.listings.intake import from_row
from gable.pipeline.runner import Runner
from gable.sheets import repository as repo


def _submission(**over: str) -> repo.Submission:
    row = [
        over.get("ts", "8/11/2026 09:00:00"),
        over.get("email", "lolo@cornerhouserealty.com"),
        over.get("name", "Lolo Simmons"),
        "ack",
        over.get("request_type", "New Listing"),
        "",
        "",
        "",
        "",
        "",
        "Static",
        over.get("address", "7940 Oakwood Rd, Glen Burnie, MD 21061"),
        "",
        over.get("details", ""),
        over.get("open_house", ""),
        over.get("new_price", ""),
        over.get("closing_price", ""),
    ]
    return repo.Submission(
        response_row_id=over.get("rid", "rid-1"),
        sheet_row=100,
        submitted_at=row[0],
        intake=from_row(row),
        content_hash="hash",
    )


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = connect(Path(tempfile.mkdtemp()) / "g.db")
    apply_migrations(connection)
    return connection


class Recorder:
    """Captures what the runner tried to do."""

    def __init__(self, slide_text: list[str] | None = None) -> None:
        """Start with a template whose text the runner will resolve."""
        self.said: list[str] = []
        self.filled: dict[str, str] = {}
        self.copied = False
        self.slide_text = slide_text or [
            "[PROPERTY ADDRESS]",
            "[PRICE]",
            "[ 4 BEDS ]",
            "[ 4 BATHS ]",
            "[ SQFT ]",
            "AGENT NAME",
            "Phone",
        ]
        self.output_text: list[str] = []

    def say(self, text: str, thread: str | None = None) -> str:  # noqa: ARG002
        """Record a message and hand back a thread id."""
        self.said.append(text)
        return "1786.0"

    def pick(self, category: str) -> tuple[str, str]:
        """Always find a template."""
        return ("tmpl-1", f"{category} — Bracket Placeholders (cleanest)")

    def read(self, file_id: str) -> list[str]:
        """Template text before a fill, output text after."""
        return self.output_text if file_id == "out-1" and self.output_text else self.slide_text

    def copy(self, template_id: str, name: str) -> tuple[str, str]:  # noqa: ARG002
        """Pretend to copy, and remember that it happened."""
        self.copied = True
        return ("out-1", "https://docs.google.com/presentation/d/out-1/edit")

    def fill(self, file_id: str, pairs: dict[str, str]) -> int:  # noqa: ARG002
        """Record the replacements and simulate their effect."""
        self.filled = pairs
        self.output_text = [pairs.get(text, text) for text in self.slide_text]
        return len(pairs)


def _runner(db: sqlite3.Connection, rec: Recorder, facts: Facts | None = None) -> Runner:
    db.execute(
        "INSERT INTO salespeople (email, first_name, last_name, phone, template, synced_at)"
        " VALUES ('lolo@cornerhouserealty.com','Lolo','Simmons',"
        "'(443) 854-8554','Just Listed','now')"
        " ON CONFLICT(email) DO NOTHING"
    )
    return Runner(
        connection=db,
        say=rec.say,
        pick_template=rec.pick,
        read_slide_text=rec.read,
        copy_template=rec.copy,
        fill=rec.fill,
        research=lambda _address: (
            facts
            or Facts(
                beds="4",
                baths="3",
                square_feet="1,804",
                list_price="$515,000",
                source_url="https://redfin.test",
                confidence=0.95,
            )
        ),
    )


def _record(db: sqlite3.Connection, submission: repo.Submission) -> None:
    store_row = submission
    from gable.db import store

    store.record_submission(
        db,
        store_row.response_row_id,
        store_row.sheet_row,
        store_row.submitted_at,
        store_row.intake,
        store_row.content_hash,
    )


# --- the happy path ---------------------------------------------------------


def test_a_complete_listing_is_built_and_delivered(db: sqlite3.Connection) -> None:
    submission = _submission()
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)

    assert result.status == "delivered"
    assert rec.copied is True
    assert result.output_url.endswith("/edit")
    assert any("Open the flyer" in said for said in rec.said)


def test_researched_facts_reach_the_flyer(db: sqlite3.Connection) -> None:
    """Beds, baths and square footage are looked up, not asked for."""
    submission = _submission()
    _record(db, submission)
    rec = Recorder()
    _runner(db, rec).run(submission)

    assert rec.filled["[ 4 BEDS ]"] == "4"
    assert rec.filled["[ SQFT ]"] == "1,804"
    assert rec.filled["[PROPERTY ADDRESS]"] == "7940 Oakwood Rd, Glen Burnie, MD 21061"


def test_the_agent_phone_comes_from_the_roster(db: sqlite3.Connection) -> None:
    submission = _submission()
    _record(db, submission)
    rec = Recorder()
    _runner(db, rec).run(submission)
    assert rec.filled["Phone"] == "(443) 854-8554"


def test_researched_facts_are_cached_for_next_time(db: sqlite3.Connection) -> None:
    """The same property comes back as a listing, an open house and a sale."""
    submission = _submission()
    _record(db, submission)
    _runner(db, Recorder()).run(submission)

    from gable.db import store

    assert store.recall_facts(db, "7940 Oakwood Rd, Glen Burnie, MD 21061")["beds"] == "4"


# --- it asks rather than guessing -------------------------------------------


def test_sold_with_no_closing_price_stops_and_asks(db: sqlite3.Connection) -> None:
    submission = _submission(request_type="Sold", rid="rid-sold")
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)

    assert result.status == "needs_info"
    assert result.needs_a_human is True
    assert rec.copied is False, "nothing should be built while a question is open"
    assert "closing price" in rec.said[0].lower()


def test_an_unusable_address_stops_and_asks(db: sqlite3.Connection) -> None:
    submission = _submission(address="Google Review", rid="rid-bad")
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)

    assert result.status == "needs_info"
    assert rec.copied is False


def test_research_that_finds_nothing_asks(db: sqlite3.Connection) -> None:
    submission = _submission(rid="rid-nofacts")
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec, facts=Facts()).run(submission)

    assert result.status == "needs_info"
    assert "could not find" in rec.said[0].lower()


def test_one_named_agent_is_not_ambiguous_and_still_builds(db: sqlite3.Connection) -> None:
    """Only two names with unclear roles is a question."""
    submission = _submission(details="Listed by: Stacey Abbott", rid="rid-two")
    _record(db, submission)
    assert _runner(db, Recorder()).run(submission).status == "delivered"


def test_two_agents_with_unclear_roles_stops_and_asks(db: sqlite3.Connection) -> None:
    """Row 84's shape, but with the roles left ambiguous."""
    submission = _submission(
        details="Listed by: Stacey Abbott. Co-listed by: Jason Vetter", rid="rid-amb"
    )
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)
    assert result.status == "needs_info"
    assert "listing agent" in rec.said[-1].lower()
    assert rec.copied is False


# --- the two quality passes -------------------------------------------------


def test_a_flyer_that_still_shows_a_placeholder_is_not_delivered(
    db: sqlite3.Connection,
) -> None:
    """Delivering something with a visible token is the failure to prevent."""
    submission = _submission(rid="rid-bad-render")
    _record(db, submission)

    class StubbornFill(Recorder):
        def fill(self, file_id: str, pairs: dict[str, str]) -> int:  # noqa: ARG002  # noqa: ARG002
            """Simulate a fill that silently changed nothing."""
            self.filled = pairs
            self.output_text = list(self.slide_text)
            return 0

    rec = StubbornFill()
    result = _runner(db, rec).run(submission)
    assert result.status == "needs_review"
    assert "I rendered it, but" in rec.said[-1]


# --- every exit records a status --------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "delivered"),
        ({"request_type": "Sold"}, "needs_info"),
        ({"address": "Google Review"}, "needs_info"),
        ({"request_type": "End of Year Brag Post"}, "needs_info"),
    ],
)
def test_every_path_reaches_a_recorded_status(
    db: sqlite3.Connection, kwargs: dict[str, str], expected: str
) -> None:
    """AGENTS.md 6: a listing's state must be explainable from the log."""
    submission = _submission(rid=f"rid-{expected}-{len(kwargs)}", **kwargs)
    _record(db, submission)
    result = _runner(db, Recorder()).run(submission)
    assert result.status == expected

    row = db.execute("SELECT status FROM runs WHERE run_id = ?", (result.run_id,)).fetchone()
    assert row["status"] == expected
    events = db.execute(
        "SELECT COUNT(*) AS n FROM run_events WHERE run_id = ?", (result.run_id,)
    ).fetchone()["n"]
    assert events >= 2, "opening and the outcome must both be logged"


def test_an_unexpected_failure_is_recorded_not_raised(db: sqlite3.Connection) -> None:
    """Raising would leave the database disagreeing with reality."""
    submission = _submission(rid="rid-boom")
    _record(db, submission)

    class Exploding(Recorder):
        def copy(self, template_id: str, name: str) -> tuple[str, str]:  # noqa: ARG002  # noqa: ARG002
            """Fail the way a Drive outage would."""
            msg = "drive is down"
            raise RuntimeError(msg)

    result = _runner(db, Exploding()).run(submission)
    assert result.status == "failed"
    assert (
        db.execute("SELECT status FROM runs WHERE run_id = ?", (result.run_id,)).fetchone()[
            "status"
        ]
        == "failed"
    )


def test_nothing_gable_says_breaks_the_house_style(db: sqlite3.Connection) -> None:
    from gable.slackapp.style import violations

    for kwargs in ({}, {"request_type": "Sold"}, {"address": "Google Review"}):
        submission = _submission(rid=f"rid-style-{abs(hash(str(kwargs)))}", **kwargs)
        _record(db, submission)
        rec = Recorder()
        _runner(db, rec).run(submission)
        for said in rec.said:
            assert not violations(said), (said, violations(said))


def test_a_template_without_a_field_does_not_fail_the_check(db: sqlite3.Connection) -> None:
    """Judging against values the design has no slot for would fail every render.

    The email is not on this template. That is the design's choice, not a defect,
    and it must not stop delivery.
    """
    submission = _submission(rid="rid-noemail")
    _record(db, submission)
    rec = Recorder(slide_text=["[PROPERTY ADDRESS]", "AGENT NAME"])
    result = _runner(db, rec).run(submission)
    assert result.status == "delivered"
