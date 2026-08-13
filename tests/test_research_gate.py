"""Selected-source scoping for paid property research."""

from __future__ import annotations

from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings import enrich
from gable.listings.enrich import Facts
from gable.pipeline import research_gate
from gable.pipeline.orchestrator import Outcome
from gable.slides.fields import Resolution
from tests.runner_support import submission


def test_price_field_maps_to_cached_list_price_name() -> None:
    resolution = Resolution(fields={"price": "[PRICE]"})
    assert research_gate.required_public_facts(resolution) == frozenset({"list_price"})


def test_cached_required_fact_avoids_research_call(tmp_path: Path) -> None:
    connection = connect(tmp_path / "research-gate.db")
    apply_migrations(connection)
    item = submission()
    store.remember_facts(
        connection,
        item.intake.address,
        {"beds": "4"},
        "https://example.test/property",
        0.9,
    )
    calls: list[str] = []

    def research(address: str, _fields: frozenset[str]) -> Facts:
        calls.append(address)
        return Facts()

    step, known = research_gate.resolve(
        connection,
        item.intake,
        Resolution(fields={"beds": "[ 4 BEDS ]"}),
        research,
    )

    assert step.outcome is Outcome.BUILD
    assert known["beds"] == "4"
    assert calls == []


def test_live_lookup_queries_and_returns_only_selected_source_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    def search(query: str, _api_key: str, limit: int = 4) -> list[dict[str, str]]:
        queries.append(query)
        assert limit == 4
        return [
            {
                "url": "https://example.test/property",
                "markdown": "4 bedrooms, 47 bathrooms, 1,804 square feet, $515,000",
            }
        ]

    monkeypatch.setattr(enrich, "_search", search)

    found = enrich.look_up(
        "123 Main St, Baltimore, MD 21201",
        "test-key",
        frozenset({"beds"}),
    )

    assert queries == ["123 Main St, Baltimore, MD 21201 bedrooms"]
    assert found.beds == "4"
    assert found.baths == ""
    assert found.square_feet == ""
    assert found.list_price == ""
    assert found.caveats == []
