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


def test_a_stated_fact_answers_the_question_without_paying_for_research(
    tmp_path: Path,
) -> None:
    """Chase answered "List price is $200,000" and the run stayed paused anyway.

    `resolve` started from an empty `known` and trusted only a freshly proven
    web result, so the answer was acknowledged in Slack and discarded. The run
    asked for the same number on every attempt.
    """
    connection = connect(tmp_path / "supplied.db")
    apply_migrations(connection)
    item = submission()
    store.remember_supplied_fact(connection, item.intake.address, "list_price", "$200,000")
    calls: list[str] = []

    def research(address: str, _fields: frozenset[str]) -> Facts:
        calls.append(address)
        return Facts()

    step, known = research_gate.resolve(
        connection,
        item.intake,
        Resolution(fields={"price": "[ PRICE ]"}),
        research,
    )

    assert step.outcome is Outcome.BUILD
    assert known["list_price"] == "$200,000"
    assert calls == [], "a stated fact must not trigger a paid lookup"


def test_a_later_lookup_cannot_overwrite_what_a_person_stated(tmp_path: Path) -> None:
    """Research still runs for the other gaps; it must not rewrite the answer.

    A missing square footage triggers a lookup even after the price is stated,
    and the listing page's own price must not replace the one given in Slack.
    """
    connection = connect(tmp_path / "supplied-merge.db")
    apply_migrations(connection)
    item = submission()
    store.remember_supplied_fact(connection, item.intake.address, "list_price", "$200,000")

    def research(_address: str, _fields: frozenset[str]) -> Facts:
        return Facts(
            square_feet="3,332",
            list_price="$975,000",
            source_url="https://example.test/property",
            confidence=0.9,
            identity_verified=True,
        )

    _step, known = research_gate.resolve(
        connection,
        item.intake,
        Resolution(fields={"price": "[ PRICE ]", "square_feet": "[ SQFT ]"}),
        research,
    )

    assert known["list_price"] == "$200,000", "the person outranks the listing page"
    assert known["square_feet"] == "3,332"


def test_an_approved_blank_stops_the_gate_asking_the_same_question_again(
    tmp_path: Path,
) -> None:
    """Once a person says to build anyway, the gap is a blank, not a question."""
    connection = connect(tmp_path / "released.db")
    apply_migrations(connection)
    item = submission()

    def research(_address: str, _fields: frozenset[str]) -> Facts:
        return Facts()

    asked, _known = research_gate.resolve(
        connection,
        item.intake,
        Resolution(fields={"price": "[ PRICE ]"}),
        research,
    )
    released, known = research_gate.resolve(
        connection,
        item.intake,
        Resolution(fields={"price": "[ PRICE ]"}),
        research,
        allow_blank_fields=True,
    )

    assert asked.outcome is Outcome.ASK
    assert released.outcome is Outcome.BUILD
    assert known.get("list_price", "") == "", "released means blank, never invented"


# --- the gate does not re-ask what somebody answered -----------------------


def test_an_open_house_already_answered_is_not_asked_for_again(tmp_path: Path) -> None:
    """The gate re-runs the coherence check, which reads the form's own column.

    Gable asked Jay Hinish's listing for its open house date and time, was
    given it, and asked again.
    """
    from gable.db import store
    from gable.db.schema import apply_migrations, connect
    from gable.listings.intake import Intake
    from gable.pipeline import research_gate
    from gable.pipeline.orchestrator import Outcome
    from gable.slides.fields import Resolution

    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    intake = Intake(
        agent_email="a@example.com",
        agent_name="Avery Agent",
        request_type="Open House",
        address="1 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )
    store.remember_supplied_fact(
        connection, intake.address, "open_house", "Saturday, Aug 22, 2026 1-3PM"
    )

    step, _known = research_gate.resolve(
        connection,
        intake,
        Resolution(fields={"open_house": "Sunday, Aug 2, 2026"}),
        lambda _address, _fields: Facts(),
        lambda: None,
    )

    assert step.outcome is not Outcome.ASK
    connection.close()


def test_an_open_house_nobody_answered_is_still_asked_for(tmp_path: Path) -> None:
    from gable.db.schema import apply_migrations, connect
    from gable.listings.intake import Intake
    from gable.pipeline import research_gate
    from gable.pipeline.orchestrator import Outcome
    from gable.slides.fields import Resolution

    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    intake = Intake(
        agent_email="a@example.com",
        agent_name="Avery Agent",
        request_type="Open House",
        address="1 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )

    step, _known = research_gate.resolve(
        connection,
        intake,
        Resolution(fields={"open_house": "Sunday, Aug 2, 2026"}),
        lambda _address, _fields: Facts(),
        lambda: None,
    )

    assert step.outcome is Outcome.ASK
    assert "open house date and time" in [q.field_name for q in step.questions]
    connection.close()
