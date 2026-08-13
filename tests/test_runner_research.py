"""Runner ordering and source-scoped property research regressions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gable.agents.website import OfficialProfile, ProfileLookup
from gable.db.schema import apply_migrations, connect
from gable.listings.enrich import Facts
from tests.runner_support import Recorder
from tests.runner_support import record as _record
from tests.runner_support import runner as _runner
from tests.runner_support import submission as _submission


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


def test_source_without_property_fact_fields_never_spends_on_property_research(
    db: sqlite3.Connection,
) -> None:
    """The selected design, not a global listing schema, decides paid lookup scope."""
    submission = _submission(rid="rid-no-public-fact-fields")
    _record(db, submission)
    rec = Recorder(
        slide_text=["[PROPERTY ADDRESS]", "AGENT NAME", "Phone"],
        template_label="Sold",
    )
    runner = _runner(db, rec)
    calls: list[str] = []

    def research(address: str, _fields: frozenset[str]) -> Facts:
        calls.append(address)
        return Facts()

    runner.research = research

    result = runner.run(submission)

    assert result.status == "delivered"
    assert calls == []
    assert set(rec.filled) == {"[PROPERTY ADDRESS]", "AGENT NAME", "Phone"}


def test_only_the_selected_source_s_missing_public_fields_trigger_research(
    db: sqlite3.Connection,
) -> None:
    """One Firecrawl call can return more, but it is made only for a displayed gap."""
    submission = _submission(rid="rid-beds-only-research")
    _record(db, submission)
    rec = Recorder(
        slide_text=["[PROPERTY ADDRESS]", "[ 4 BEDS ]", "AGENT NAME", "Phone"],
        template_label="Beds Design",
    )
    runner = _runner(db, rec)
    calls: list[str] = []

    def research(address: str, fields: frozenset[str]) -> Facts:
        calls.append(address)
        assert fields == frozenset({"beds"})
        return Facts(
            beds="4",
            baths="3",
            square_feet="1,804",
            source_url="https://example.test/property",
            confidence=0.95,
            identity_verified=True,
        )

    runner.research = research

    result = runner.run(submission)

    assert result.status == "delivered"
    assert calls == [submission.intake.address]
    assert rec.filled["[ 4 BEDS ]"] == "4"
    assert all("baths" not in message for message in result.said)
    assert all("square feet" not in message for message in result.said)


def test_missing_source_blocks_before_property_research(db: sqlite3.Connection) -> None:
    """No template means there is no evidence that any public fact is needed."""
    submission = _submission(rid="rid-missing-source-before-research")
    _record(db, submission)

    class MissingSource(Recorder):
        def pick(self, category: str, intake: object = None) -> tuple[str, str]:  # noqa: ARG002
            return ("", "")

    rec = MissingSource()
    runner = _runner(db, rec)
    calls: list[str] = []

    def research(address: str, _fields: frozenset[str]) -> Facts:
        calls.append(address)
        return Facts()

    runner.research = research

    result = runner.run(submission)

    assert result.status == "needs_template"
    assert calls == []
    assert rec.copied is False


def test_title_and_headshot_prerequisites_precede_slack(
    db: sqlite3.Connection,
) -> None:
    """Source-specific identity proof finishes before the paid listing lookup."""
    submission = _submission(
        rid="rid-prerequisite-order",
        email="mike@cornerhouserealty.com",
        name="Mike Kulnich",
        request_type="Sold",
    )
    _record(db, submission)
    db.execute(
        "INSERT INTO salespeople "
        "(email, first_name, last_name, phone, template, synced_at) "
        "VALUES (?, ?, ?, ?, '', 'now')",
        ("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
    )

    class OrderedRecorder(Recorder):
        def say(self, text: str, thread: str | None = None) -> str:
            order.append("slack")
            return super().say(text, thread)

    order: list[str] = []
    rec = OrderedRecorder(
        slide_text=["[PROPERTY ADDRESS]", "[ 4 BEDS ]", "AGENT NAME", "Realtor"],
        template_label="Sold With Beds",
    )
    runner = _runner(db, rec)

    def official(name: str, email: str) -> ProfileLookup:
        order.append("title")
        return ProfileLookup(
            profile=OfficialProfile(
                name=name,
                email=email,
                phone="410.456.3564",
                title="REALTOR®",
                source_url="https://cornerhouserealty.com/mike-kulnich/",
            )
        )

    def headshot(_name: str) -> str:
        order.append("headshot")
        return "https://example.test/mike.jpg"

    def research(_address: str, fields: frozenset[str]) -> Facts:
        order.append("research")
        assert fields == frozenset({"beds"})
        return Facts(
            beds="4",
            source_url="https://example.test/property",
            confidence=0.8,
            identity_verified=True,
        )

    runner.official_contact_lookup = official
    runner.headshot_for = headshot
    runner.research = research

    result = runner.run(submission)

    assert result.status == "delivered"
    assert order.index("title") < order.index("research")
    assert order.index("title") < order.index("slack")
    assert order.index("headshot") < order.index("slack")
