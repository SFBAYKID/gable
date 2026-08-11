"""The shared spend ledger stops calls before Chase's hard ceiling."""

from __future__ import annotations

import sqlite3

import pytest

from gable import spend
from gable.db.schema import apply_migrations, connect
from gable.listings.enrich import Facts
from gable.pipeline import runner as runner_module


@pytest.fixture
def db() -> sqlite3.Connection:
    """Return a migrated in-memory spend ledger."""
    connection = connect(":memory:")
    apply_migrations(connection)
    return connection


def test_guarded_call_records_its_conservative_reservation(db: sqlite3.Connection) -> None:
    estimate = spend.Estimate("openai", "gpt-5-mini", 0.01, "conversation")

    result = spend.guarded_call(db, estimate, lambda: "finished", run_id="run-1")

    assert result == "finished"
    assert spend.total_spent(db) == pytest.approx(0.01)
    row = db.execute("SELECT service, model, run_id, note FROM spend").fetchone()
    assert tuple(row) == ("openai", "gpt-5-mini", "run-1", "conversation")


def test_guarded_call_records_a_vendor_failure(db: sqlite3.Connection) -> None:
    estimate = spend.Estimate("firecrawl", "search", 0.01, "property search")

    def fail() -> str:
        raise RuntimeError("vendor failed after accepting the request")

    with pytest.raises(RuntimeError, match="vendor failed"):
        spend.guarded_call(db, estimate, fail)

    assert spend.total_spent(db) == pytest.approx(0.01)


def test_guarded_call_never_invokes_vendor_at_the_ceiling(db: sqlite3.Connection) -> None:
    spend.record(
        db,
        spend.Estimate("test", "prior", spend.CEILING_USD - 0.005, "prior spend"),
    )
    called = False

    def vendor() -> str:
        nonlocal called
        called = True
        return "should not run"

    with pytest.raises(spend.BudgetExceededError):
        spend.guarded_call(
            db,
            spend.Estimate("openai", "gpt-5-mini", 0.01),
            vendor,
        )

    assert called is False
    assert spend.total_spent(db) == pytest.approx(spend.CEILING_USD - 0.005)


def test_live_research_reserves_and_records_one_firecrawl_search(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def lookup(address: str, api_key: str) -> Facts:
        calls.append(f"{address}:{api_key}")
        return Facts(beds="4")

    monkeypatch.setattr(runner_module, "look_up", lookup)

    found = runner_module.default_research("key", db)("123 Main St")

    assert found.beds == "4"
    assert calls == ["123 Main St:key"]
    assert spend.total_spent(db) == pytest.approx(spend.FIRECRAWL_PER_SEARCH)
