"""Tests for the domain types.

Weighted toward the invariants that protect a flyer from shipping wrong: the
idempotency key's stability, and the impossibility of constructing a synthetic
photo that is not flagged as one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from gable.models import (
    MAX_ERROR_CHARS,
    RESPONSE_ROW_ID_CHARS,
    RUNS_HEADERS,
    AgentProfile,
    Listing,
    PhotoResult,
    PhotoSource,
    RunRecord,
    RunStatus,
    derive_response_row_id,
    normalize_for_identity,
    to_iso8601,
    utc_now,
)

MOMENT = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)


def _listing(**overrides: object) -> Listing:
    base: dict[str, object] = {
        "response_row_id": "abc123",
        "submitted_at": MOMENT,
        "agent_email": "jane@brokerage.com",
        "agent_name": "Jane Doe",
        "address": "123 Anywhere St, Any City, ST 12345",
        "price_display": "$1,200,000",
    }
    base.update(overrides)
    return Listing(**base)  # type: ignore[arg-type]


# --- idempotency key --------------------------------------------------------


def test_row_id_is_stable_across_calls() -> None:
    args = ("2026-08-10 14:30:00", "jane@brokerage.com", "123 Anywhere St")
    assert derive_response_row_id(*args) == derive_response_row_id(*args)


def test_row_id_has_the_documented_length() -> None:
    row_id = derive_response_row_id("t", "e", "a")
    assert len(row_id) == RESPONSE_ROW_ID_CHARS
    assert row_id == row_id.lower()


def test_row_id_ignores_cosmetic_retyping() -> None:
    """A whitespace or case edit must not rebuild a flyer that already shipped."""
    a = derive_response_row_id("2026-08-10 14:30:00", "Jane@Brokerage.com", "123 Main St")
    b = derive_response_row_id("2026-08-10 14:30:00", "jane@brokerage.com", "123  main  st")
    assert a == b


@pytest.mark.parametrize(
    ("timestamp", "email", "address"),
    [
        ("2026-08-10 14:31:00", "jane@brokerage.com", "123 Main St"),
        ("2026-08-10 14:30:00", "john@brokerage.com", "123 Main St"),
        ("2026-08-10 14:30:00", "jane@brokerage.com", "124 Main St"),
    ],
)
def test_row_id_changes_when_any_field_changes(timestamp: str, email: str, address: str) -> None:
    baseline = derive_response_row_id("2026-08-10 14:30:00", "jane@brokerage.com", "123 Main St")
    assert derive_response_row_id(timestamp, email, address) != baseline


def test_row_id_cannot_be_confused_by_field_boundaries() -> None:
    """Concatenation must not let one field impersonate a split across two."""
    a = derive_response_row_id("ab", "c", "d")
    b = derive_response_row_id("a", "bc", "d")
    assert a != b


def test_normalize_for_identity_collapses_whitespace() -> None:
    assert normalize_for_identity("  A   B\tC\n") == "a b c"


# --- timestamps -------------------------------------------------------------


def test_utc_now_is_timezone_aware() -> None:
    assert utc_now().tzinfo is not None


def test_iso8601_round_trips_utc() -> None:
    assert to_iso8601(MOMENT) == "2026-08-10T14:30:00+00:00"


def test_iso8601_converts_a_non_utc_zone() -> None:
    eastern = MOMENT.astimezone(timezone(timedelta(hours=-4)))
    assert to_iso8601(eastern) == "2026-08-10T14:30:00+00:00"


def test_naive_datetime_is_treated_as_utc_not_local() -> None:
    """Documented ASSUMPTION — pinned so a change to it is deliberate."""
    assert to_iso8601(datetime(2026, 8, 10, 14, 30)).endswith("+00:00")


# --- status semantics -------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "terminal", "paused"),
    [
        (RunStatus.PENDING, False, False),
        (RunStatus.NEEDS_PHOTO, False, True),
        (RunStatus.NEEDS_TEMPLATE, False, True),
        (RunStatus.READY, False, False),
        (RunStatus.DELIVERED, True, False),
        (RunStatus.FAILED, True, False),
    ],
)
def test_status_semantics(status: RunStatus, terminal: bool, paused: bool) -> None:
    assert status.is_terminal is terminal
    assert status.is_paused is paused


def test_paused_states_are_not_terminal() -> None:
    """AGENTS.md 6: they wait for Carmen indefinitely and re-enter on /gable run.

    If these were terminal the poller would skip them forever and a listing
    waiting on a photo would never come back.
    """
    for status in (RunStatus.NEEDS_PHOTO, RunStatus.NEEDS_TEMPLATE):
        assert not status.is_terminal


# --- photo invariants -------------------------------------------------------


def test_generated_photo_cannot_be_constructed_unflagged() -> None:
    """The whole class of "synthetic image with no trace" bug, removed."""
    with pytest.raises(ValueError, match="ai_generated"):
        PhotoResult(url="https://x/y.jpg", source=PhotoSource.GENERATED, confidence=1.0)


def test_generated_photo_with_the_flag_is_fine() -> None:
    photo = PhotoResult(
        url="https://x/y.jpg", source=PhotoSource.GENERATED, confidence=1.0, ai_generated=True
    )
    assert photo.source.is_synthetic is True


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
def test_confidence_must_be_a_probability(bad: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        PhotoResult(url="https://x/y.jpg", source=PhotoSource.WEB, confidence=bad)


def test_low_confidence_web_photo_does_not_meet_threshold() -> None:
    """Below threshold routes to "ask Carmen", never onto a flyer."""
    photo = PhotoResult(url="https://x/y.jpg", source=PhotoSource.BROKERAGE, confidence=0.61)
    assert photo.meets(0.75) is False


def test_human_supplied_photo_always_meets_the_threshold() -> None:
    """AGENTS.md 4.4: Gable does not second-guess a photo a person chose."""
    photo = PhotoResult(url="https://x/y.jpg", source=PhotoSource.CARMEN, confidence=0.0)
    assert photo.meets(0.99) is True


def test_form_upload_counts_as_human_supplied() -> None:
    assert PhotoSource.FORM.is_human_supplied is True
    assert PhotoSource.WEB.is_human_supplied is False


# --- agent profile ----------------------------------------------------------


def test_agent_email_is_normalized_on_construction() -> None:
    """Case and whitespace must not manufacture a second, unknown agent."""
    agent = AgentProfile(agent_email="  Jane@Brokerage.COM ", agent_name="Jane Doe")
    assert agent.agent_email == "jane@brokerage.com"


def test_display_template_prefers_the_human_label() -> None:
    agent = AgentProfile(
        agent_email="j@b.com",
        agent_name="Jane",
        slides_template_id="1a2B3cD4eF5gH",
        template_label="Template 3 — Luxury Estate",
    )
    assert agent.display_template == "Template 3 — Luxury Estate"


def test_display_template_warns_when_nothing_is_mapped() -> None:
    agent = AgentProfile(agent_email="j@b.com", agent_name="Jane")
    assert "no template" in agent.display_template


def test_a_label_without_a_file_id_is_not_a_usable_template() -> None:
    """Looks mapped, isn't. Must pause as needs_template, not fail mid-render."""
    agent = AgentProfile(agent_email="j@b.com", agent_name="Jane", template_label="Template 1")
    assert agent.has_template is False
    assert (
        AgentProfile(
            agent_email="j@b.com", agent_name="Jane", slides_template_id="1a2B3c"
        ).has_template
        is True
    )


# --- listing ----------------------------------------------------------------


def test_listing_construction_never_raises_on_missing_data() -> None:
    """A missing description must not take down the batch."""
    listing = _listing(description="", price_display="")
    assert listing.has_problems is False
    assert listing.is_flyer_ready is False


def test_flyer_ready_requires_every_display_field() -> None:
    assert _listing().is_flyer_ready is True
    assert _listing(address="").is_flyer_ready is False
    assert _listing(agent_name="").is_flyer_ready is False
    assert _listing(price_display="").is_flyer_ready is False


def test_with_problem_appends_without_mutating() -> None:
    original = _listing()
    updated = original.with_problem("phone could not be parsed")
    assert original.problems == ()
    assert updated.problems == ("phone could not be parsed",)
    assert updated.has_problems is True


def test_with_problem_accumulates() -> None:
    listing = _listing().with_problem("a").with_problem("b")
    assert listing.problems == ("a", "b")


def test_listing_is_frozen() -> None:
    with pytest.raises(AttributeError):
        _listing().address = "somewhere else"  # type: ignore[misc]


# --- run record -------------------------------------------------------------


def _run(**overrides: object) -> RunRecord:
    base: dict[str, object] = {
        "run_id": "01J000000000000000000000",
        "response_row_id": "abc123",
        "address": "123 Anywhere St",
        "status": RunStatus.READY,
        "created_at": MOMENT,
        "updated_at": MOMENT,
    }
    base.update(overrides)
    return RunRecord(**base)  # type: ignore[arg-type]


def test_row_matches_the_documented_header_order() -> None:
    row = _run().to_row()
    assert len(row) == len(RUNS_HEADERS)
    assert row[RUNS_HEADERS.index("status")] == "ready"
    assert row[RUNS_HEADERS.index("address")] == "123 Anywhere St"


def test_booleans_render_for_the_sheet_not_for_python() -> None:
    row = _run(ai_enhanced=True).to_row()
    assert row[RUNS_HEADERS.index("ai_enhanced")] == "TRUE"
    assert row[RUNS_HEADERS.index("ai_generated")] == "FALSE"


def test_absent_photo_source_is_an_empty_cell_not_the_string_none() -> None:
    assert _run().to_row()[RUNS_HEADERS.index("photo_source")] == ""


def test_timestamps_are_iso8601_utc() -> None:
    row = _run().to_row()
    assert row[RUNS_HEADERS.index("created_at")] == "2026-08-10T14:30:00+00:00"


def test_long_error_is_truncated_with_a_marker() -> None:
    """One enormous traceback must not make the tab unreadable."""
    record = _run(status=RunStatus.FAILED, error="x" * (MAX_ERROR_CHARS * 3))
    assert len(record.error) == MAX_ERROR_CHARS
    assert record.error.endswith("…")


def test_short_error_is_left_alone() -> None:
    assert _run(error="Sheet unreachable").error == "Sheet unreachable"


def test_generated_photo_source_requires_the_runs_flag() -> None:
    """The Runs tab is where the record of a synthetic image has to survive."""
    with pytest.raises(ValueError, match="ai_generated"):
        _run(photo_source=PhotoSource.GENERATED, ai_generated=False)


def test_generated_photo_source_with_the_flag_is_accepted() -> None:
    record = _run(photo_source=PhotoSource.GENERATED, ai_generated=True)
    assert record.to_row()[RUNS_HEADERS.index("ai_generated")] == "TRUE"


def test_created_at_defaults_to_now_when_omitted() -> None:
    before = utc_now()
    record = RunRecord(run_id="r", response_row_id="x", address="a", status=RunStatus.PENDING)
    assert before <= record.created_at <= utc_now()
