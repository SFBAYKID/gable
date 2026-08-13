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
    assert research_gate.required_public_facts(resolution, submission().intake) == frozenset(
        {"list_price"}
    )


def test_sold_price_field_never_maps_to_public_list_price() -> None:
    item = submission(request_type="Sold", closing_price="")
    assert (
        research_gate.required_public_facts(Resolution(fields={"price": "[PRICE]"}), item.intake)
        == frozenset()
    )


def test_legacy_cached_fact_is_rechecked_with_current_property_identity(tmp_path: Path) -> None:
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
        return Facts(
            beds="5",
            source_url="https://example.test/property",
            confidence=0.8,
            identity_verified=True,
        )

    step, known = research_gate.resolve(
        connection,
        item.intake,
        Resolution(fields={"beds": "[ 4 BEDS ]"}),
        research,
    )

    assert step.outcome is Outcome.BUILD
    assert known["beds"] == "5"
    assert calls == [item.intake.address]


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
                "markdown": (
                    "123 Main St, Baltimore, MD 21201 has 4 bedrooms, "
                    "47 bathrooms, 1,804 square feet, list price $515,000"
                ),
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
    assert found.identity_verified


def test_plausible_numbers_without_exact_address_identity_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enrich,
        "_search",
        lambda *_args, **_kwargs: [
            {
                "url": "https://example.test/different-property",
                "markdown": "4 bedrooms, 3 bathrooms, 1,804 square feet, $515,000",
            }
        ],
    )

    found = enrich.look_up(
        "123 Main St, Baltimore, MD 21201",
        "test-key",
        frozenset({"beds"}),
    )

    assert found.is_empty
    assert not found.identity_verified


def test_a_nearby_but_different_street_number_cannot_prove_property_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enrich,
        "_search",
        lambda *_args, **_kwargs: [
            {
                "url": "https://example.test/nearby-property",
                "markdown": (
                    "1123 Main Street, Baltimore, MD 21201 has 9 bedrooms and list price $915,000"
                ),
            }
        ],
    )

    found = enrich.look_up(
        "123 Main St, Baltimore, MD 21201",
        "test-key",
        frozenset({"beds", "list_price"}),
    )

    assert found.is_empty
    assert not found.identity_verified


def test_an_earlier_listing_on_the_same_result_cannot_donate_its_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enrich,
        "_search",
        lambda *_args, **_kwargs: [
            {
                "url": "https://example.test/search-results",
                "markdown": (
                    "999 Other Rd, Baltimore, MD 21201 has 9 bedrooms and "
                    "list price $915,000\n"
                    "123 Main St, Baltimore, MD 21201 has 4 bedrooms and "
                    "list price $515,000"
                ),
            }
        ],
    )

    found = enrich.look_up(
        "123 Main St, Baltimore, MD 21201",
        "test-key",
        frozenset({"beds", "list_price"}),
    )

    assert found.beds == "4"
    assert found.list_price == "$515,000"
    assert found.identity_verified


def test_only_an_explicit_listing_price_is_extracted_from_the_property_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enrich,
        "_search",
        lambda *_args, **_kwargs: [
            {
                "url": "https://example.test/property",
                "markdown": (
                    "123 Main St, Baltimore, MD 21201. $3,100 monthly payment. "
                    "The list price is $515,000."
                ),
            }
        ],
    )

    found = enrich.look_up(
        "123 Main St, Baltimore, MD 21201",
        "test-key",
        frozenset({"list_price"}),
    )

    assert found.list_price == "$515,000"


def test_an_unlabelled_dollar_amount_is_not_a_public_list_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enrich,
        "_search",
        lambda *_args, **_kwargs: [
            {
                "url": "https://example.test/property",
                "markdown": "123 Main St, Baltimore, MD 21201. $515,000.",
            }
        ],
    )

    found = enrich.look_up(
        "123 Main St, Baltimore, MD 21201",
        "test-key",
        frozenset({"list_price"}),
    )

    assert found.list_price == ""


def test_malformed_firecrawl_entries_are_skipped_without_aborting_valid_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enrich,
        "_search",
        lambda *_args, **_kwargs: [
            None,
            "not an object",
            ["also", "not", "an", "object"],
            {
                "url": "https://example.test/property",
                "markdown": "123 Main St, Baltimore, MD 21201 has 4 bedrooms",
            },
        ],
    )

    found = enrich.look_up(
        "123 Main St, Baltimore, MD 21201",
        "test-key",
        frozenset({"beds"}),
    )

    assert found.beds == "4"
    assert found.identity_verified


def test_blank_source_url_is_not_authoritative() -> None:
    assert not enrich.has_authoritative_source(Facts(beds="4", identity_verified=True))
