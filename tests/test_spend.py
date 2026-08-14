"""The shared spend ledger stops calls before Chase's hard ceiling."""

from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gable import spend
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings import enrich as enrich_module
from gable.listings.enrich import Facts
from gable.listings.intake import Intake


@pytest.fixture
def db() -> sqlite3.Connection:
    """Return a migrated in-memory spend ledger."""
    connection = connect(":memory:")
    apply_migrations(connection)
    return connection


def _listing_run(connection: sqlite3.Connection, response_id: str = "response-spend") -> str:
    """Create one real listing/run parent for operation-limit tests."""
    store.record_submission(
        connection,
        response_id,
        48,
        response_id,
        Intake(
            agent_email="agent@example.com",
            agent_name="Agent Example",
            request_type="Sold",
            address="1 Main St, Baltimore, MD 21201",
            post_details="",
            open_house="",
            new_price="",
            closing_price="",
            extra_notes="",
            side="",
            notes="",
        ),
    )
    return store.start_run(connection, response_id).run_id


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


def test_operation_count_includes_failed_paid_attempts(db: sqlite3.Connection) -> None:
    estimate = spend.Estimate(
        "openai",
        "gpt-image-2",
        spend.IMAGE_EDIT_RESERVE_USD,
        spend.IMAGE_UPSCALE_DETAIL,
    )

    def fail() -> bytes:
        raise RuntimeError("provider accepted the edit before failing")

    with pytest.raises(RuntimeError):
        spend.guarded_call(db, estimate, fail, run_id="run-1")

    assert spend.operation_count(db, "run-1", spend.IMAGE_UPSCALE_DETAIL) == 1
    assert spend.operation_count(db, "run-2", spend.IMAGE_UPSCALE_DETAIL) == 0


def test_an_unreleased_vendor_failure_consumes_the_image_allowance(
    db: sqlite3.Connection,
) -> None:
    """A crash or ordinary vendor failure never releases itself automatically."""
    run_id = _listing_run(db)
    estimate = spend.Estimate(
        "openai",
        "gpt-image-2",
        spend.IMAGE_EDIT_RESERVE_USD,
        spend.IMAGE_UPSCALE_DETAIL,
    )
    calls = 0

    def fail() -> bytes:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider failure with unknown billing state")

    with pytest.raises(RuntimeError):
        spend.guarded_call(db, estimate, fail, run_id=run_id, max_operations=1)
    with pytest.raises(spend.OperationLimitReachedError):
        spend.guarded_call(db, estimate, fail, run_id=run_id, max_operations=1)

    assert calls == 1
    assert db.execute("SELECT COUNT(*) FROM operation_releases").fetchone()[0] == 0


def test_releasing_a_rejected_request_preserves_spend_and_allows_one_actual_call(
    db: sqlite3.Connection,
) -> None:
    run_id = _listing_run(db)
    estimate = spend.Estimate(
        "openai",
        "gpt-image-2",
        spend.IMAGE_EDIT_RESERVE_USD,
        spend.IMAGE_UPSCALE_DETAIL,
    )
    with pytest.raises(RuntimeError):
        spend.guarded_call(
            db,
            estimate,
            lambda: (_ for _ in ()).throw(RuntimeError("HTTP 400 before inference")),
            run_id=run_id,
            max_operations=1,
        )
    spend_id = int(db.execute("SELECT id FROM spend WHERE run_id = ?", (run_id,)).fetchone()[0])
    before = spend.total_spent(db)

    spend.release_rejected_image_reservation(
        db,
        spend_id,
        reason="invalid_request_dimensions",
        evidence="HTTP 400; 1088x512 is below the documented 655360 pixels",
    )
    result = spend.guarded_call(
        db,
        estimate,
        lambda: b"actual model output",
        run_id=run_id,
        max_operations=1,
    )
    with pytest.raises(spend.OperationLimitReachedError):
        spend.guarded_call(
            db,
            estimate,
            lambda: b"must not run",
            run_id=run_id,
            max_operations=1,
        )

    assert result == b"actual model output"
    assert spend.total_spent(db) == pytest.approx(before + spend.IMAGE_EDIT_RESERVE_USD)
    assert spend.operation_count(db, run_id, spend.IMAGE_UPSCALE_DETAIL) == 2


def test_reservation_release_is_append_only_and_idempotent(db: sqlite3.Connection) -> None:
    run_id = _listing_run(db)
    spend.record(
        db,
        spend.Estimate(
            "openai",
            "gpt-image-2",
            spend.IMAGE_EDIT_RESERVE_USD,
            spend.IMAGE_UPSCALE_DETAIL,
        ),
        run_id,
    )
    spend_id = int(db.execute("SELECT id FROM spend").fetchone()[0])
    kwargs = {
        "reason": "invalid_request_dimensions",
        "evidence": "HTTP 400 before inference for an invalid documented size",
    }

    spend.release_rejected_image_reservation(db, spend_id, **kwargs)
    with pytest.raises(spend.OperationReleaseError, match="already released"):
        spend.release_rejected_image_reservation(db, spend_id, **kwargs)

    release = db.execute(
        "SELECT spend_id, run_id, operation_detail, reason, evidence FROM operation_releases"
    ).fetchone()
    assert tuple(release) == (
        spend_id,
        run_id,
        spend.IMAGE_UPSCALE_DETAIL,
        kwargs["reason"],
        kwargs["evidence"],
    )


@pytest.mark.parametrize(
    ("service", "detail", "run_scoped"),
    [
        ("openai", "conversation", True),
        ("firecrawl", spend.IMAGE_UPSCALE_DETAIL, True),
        ("openai", spend.IMAGE_UPSCALE_DETAIL, False),
    ],
)
def test_only_an_exact_listing_image_reservation_can_be_released(
    db: sqlite3.Connection,
    service: str,
    detail: str,
    run_scoped: bool,
) -> None:
    run_id = _listing_run(db)
    spend.record(
        db,
        spend.Estimate(service, "test-model", 0.25, detail),
        run_id if run_scoped else "",
    )
    spend_id = int(db.execute("SELECT id FROM spend").fetchone()[0])

    with pytest.raises(spend.OperationReleaseError, match="only a listing-scoped"):
        spend.release_rejected_image_reservation(
            db,
            spend_id,
            reason="not_releasable",
            evidence="Specific evidence long enough for the audit record",
        )

    assert db.execute("SELECT COUNT(*) FROM operation_releases").fetchone()[0] == 0


def test_a_release_does_not_reset_the_listing_allowance_on_later_runs(
    db: sqlite3.Connection,
) -> None:
    first_run = _listing_run(db)
    estimate = spend.Estimate(
        "openai",
        "gpt-image-2",
        spend.IMAGE_EDIT_RESERVE_USD,
        spend.IMAGE_UPSCALE_DETAIL,
    )
    with pytest.raises(RuntimeError):
        spend.guarded_call(
            db,
            estimate,
            lambda: (_ for _ in ()).throw(RuntimeError("pre-inference rejection")),
            run_id=first_run,
            max_operations=1,
        )
    spend_id = int(db.execute("SELECT id FROM spend").fetchone()[0])
    spend.release_rejected_image_reservation(
        db,
        spend_id,
        reason="invalid_request_dimensions",
        evidence="HTTP 400 before inference for an invalid documented size",
    )
    store.set_status(db, first_run, "failed", "test attempt boundary")
    second_run = store.start_run(db, "response-spend").run_id
    spend.guarded_call(
        db,
        estimate,
        lambda: b"actual model output",
        run_id=second_run,
        max_operations=1,
    )
    store.set_status(db, second_run, "failed", "test attempt boundary")
    third_run = store.start_run(db, "response-spend").run_id

    with pytest.raises(spend.OperationLimitReachedError):
        spend.guarded_call(
            db,
            estimate,
            lambda: b"must not run",
            run_id=third_run,
            max_operations=1,
        )


def test_concurrent_workers_after_a_release_still_buy_only_one_operation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "released-operation.db"
    setup = connect(path)
    apply_migrations(setup)
    run_id = _listing_run(setup)
    estimate = spend.Estimate(
        "openai",
        "gpt-image-2",
        spend.IMAGE_EDIT_RESERVE_USD,
        spend.IMAGE_UPSCALE_DETAIL,
    )
    spend.record(setup, estimate, run_id)
    spend_id = int(setup.execute("SELECT id FROM spend").fetchone()[0])
    spend.release_rejected_image_reservation(
        setup,
        spend_id,
        reason="invalid_request_dimensions",
        evidence="HTTP 400 before inference for an invalid documented size",
    )
    setup.close()

    start = threading.Barrier(2)
    vendor_calls: list[int] = []
    call_lock = threading.Lock()

    def compete(index: int) -> str:
        connection = connect(path)
        try:
            start.wait(timeout=5)

            def vendor() -> bytes:
                with call_lock:
                    vendor_calls.append(index)
                time.sleep(0.05)
                return b"actual model output"

            try:
                spend.guarded_call(
                    connection,
                    estimate,
                    vendor,
                    run_id=run_id,
                    max_operations=1,
                )
            except spend.OperationLimitReachedError:
                return "refused"
            return "called"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, (1, 2)))

    assert sorted(outcomes) == ["called", "refused"]
    assert len(vendor_calls) == 1


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


def test_concurrent_paid_calls_cannot_cross_the_shared_ceiling(tmp_path: Path) -> None:
    """Slack workers reserve before calling, so only one can spend the headroom."""
    path = tmp_path / "gable.db"
    setup = connect(path)
    apply_migrations(setup)
    spend.record(
        setup,
        spend.Estimate("test", "prior", spend.CEILING_USD - 0.15, "prior spend"),
    )
    setup.close()

    start = threading.Barrier(2)
    vendor_calls: list[int] = []
    call_lock = threading.Lock()

    def compete(index: int) -> str:
        connection = connect(path)
        try:
            start.wait(timeout=5)

            def vendor() -> str:
                with call_lock:
                    vendor_calls.append(index)
                # The old post-call ledger left this whole interval open for a
                # second worker to pass the same stale ceiling check.
                time.sleep(0.05)
                return "called"

            try:
                return spend.guarded_call(
                    connection,
                    spend.Estimate("openai", "vision", 0.10, f"call {index}"),
                    vendor,
                )
            except spend.BudgetExceededError:
                return "refused"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, (1, 2)))

    checked = connect(path)
    assert sorted(outcomes) == ["called", "refused"]
    assert len(vendor_calls) == 1
    assert spend.total_spent(checked) == pytest.approx(spend.CEILING_USD - 0.05)
    checked.close()


def test_live_research_reserves_and_records_one_firecrawl_search(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def lookup(address: str, api_key: str, required: frozenset[str]) -> Facts:
        assert required == frozenset({"beds", "baths", "square_feet", "list_price"})
        calls.append(f"{address}:{api_key}")
        return Facts(beds="4")

    monkeypatch.setattr(enrich_module, "look_up", lookup)

    found = enrich_module.default_research("key", db)(
        "123 Main St",
        frozenset({"beds", "baths", "square_feet", "list_price"}),
    )

    assert found.beds == "4"
    assert calls == ["123 Main St:key"]
    assert spend.total_spent(db) == pytest.approx(spend.FIRECRAWL_PER_SEARCH)


def test_configure_ceiling_moves_the_ceiling_and_its_warning_together() -> None:
    """The campaign raises the ceiling; the warning threshold follows it."""
    original_ceiling = spend.CEILING_USD
    original_warn = spend.WARN_AT_USD
    try:
        spend.configure_ceiling(500)

        assert pytest.approx(500.0) == spend.CEILING_USD
        assert pytest.approx(500.0 * spend.WARN_FRACTION) == spend.WARN_AT_USD
    finally:
        spend.CEILING_USD = original_ceiling
        spend.WARN_AT_USD = original_warn


def test_configure_ceiling_refuses_a_ceiling_that_would_stop_every_call() -> None:
    """Zero is a configuration mistake, not a budget of nothing."""
    original_ceiling = spend.CEILING_USD
    original_warn = spend.WARN_AT_USD
    try:
        with pytest.raises(ValueError, match="positive"):
            spend.configure_ceiling(0)

        assert pytest.approx(original_ceiling) == spend.CEILING_USD
    finally:
        spend.CEILING_USD = original_ceiling
        spend.WARN_AT_USD = original_warn


def test_a_raised_ceiling_admits_a_call_the_default_would_refuse(
    db: sqlite3.Connection,
) -> None:
    """The guard enforces the configured ceiling, not the compiled-in default."""
    original_ceiling = spend.CEILING_USD
    original_warn = spend.WARN_AT_USD
    spend.record(
        db,
        spend.Estimate("test", "prior", spend.DEFAULT_CEILING_USD - 0.01, "prior spend"),
    )
    call = spend.Estimate("test", "campaign", 0.10, "one campaign call")
    try:
        with pytest.raises(spend.BudgetExceededError):
            spend.reserve(db, call)

        spend.configure_ceiling(500)
        spend.reserve(db, call)

        assert spend.total_spent(db) == pytest.approx(spend.DEFAULT_CEILING_USD + 0.09)
    finally:
        spend.CEILING_USD = original_ceiling
        spend.WARN_AT_USD = original_warn
