"""What testing costs, and the ceiling it must not cross.

Chase set a hard limit: **stop at $50.** An agent that can call a paid API in a
loop needs that limit in code, not in a comment (AGENTS.md §7), so `reserve()`
atomically records every allowed reservation before a paid call and raises
rather than returning a value the caller might ignore.

Each call reserves a fixed conservative amount before the vendor is reached.
These are safety estimates, not an invoice — the authority is the vendor's
dashboard, and this exists to stop a runaway, not to do accounting.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, TypeVar

#: Firecrawl bills per search rather than per token.
FIRECRAWL_PER_SEARCH: Final[float] = 0.01

#: Conservative reservations for calls whose actual token count is not known
#: until after the vendor replies. Both exceed the configured maximum-output
#: cost plus the normal prompt, so the guard stops early rather than late.
# Sol is $5 per million input tokens and $30 per million output tokens as of
# 2026-08-12. The 2,000-token output ceiling alone can cost six cents, before
# the system prompt and reasoning tokens, so the old one-cent mini-model
# reservation understated the call. Ten cents remains deliberately rounded up.
CONVERSATION_RESERVE_USD: Final[float] = 0.10
# The final gate now sends both a source photo and a render at original detail.
# Ten cents remains intentionally above the documented token-price estimate,
# including the configured output ceiling, instead of understating a two-image
# request as though it still contained only one thumbnail.
VISION_RESERVE_USD: Final[float] = 0.10
#: One medium-quality GPT Image 2 edit plus its high-fidelity input.
#: VERIFIED 2026-08-11: the official image-generation guide prices the standard
#: portrait output below this reservation. The extra headroom deliberately
#: covers input-image tokens and pricing drift without understating the ledger.
IMAGE_EDIT_RESERVE_USD: Final[float] = 0.25

#: Exact ledger detail used to enforce the one-upscale-per-listing limit.
IMAGE_UPSCALE_DETAIL: Final[str] = "conservative real-photo upscale reservation"

#: The ceiling Chase set. Reaching it stops the run rather than warning.
CEILING_USD: Final[float] = 50.0

#: Warn from here, so there is room to react before the ceiling.
WARN_AT_USD: Final[float] = 35.0


class BudgetExceededError(Exception):
    """Raised when a paid call would cross the ceiling."""


class OperationLimitReachedError(Exception):
    """Raised when one listing has already reserved its allowed paid operations."""


@dataclass(frozen=True, slots=True)
class Estimate:
    """What one call cost, in dollars."""

    service: str
    model: str
    usd: float
    detail: str = ""


_T = TypeVar("_T")


def total_spent(connection: sqlite3.Connection) -> float:
    """Everything recorded so far.

    Args:
        connection: An open database connection.

    Returns:
        Total USD.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT COALESCE(SUM(units), 0) AS total FROM spend WHERE unit_kind = 'usd'"
    ).fetchone()
    return float(row["total"] if row else 0.0)


def operation_count(connection: sqlite3.Connection, run_id: str, detail: str) -> int:
    """Count reserved paid operations of one kind for a flyer run.

    Args:
        connection: Database holding the spend ledger.
        run_id: Flyer run identity.
        detail: Exact operation marker stored in the spend note.

    Returns:
        Number of matching reservations, including failed vendor calls.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT COUNT(*) AS calls FROM spend WHERE run_id = ? AND note = ?",
        (run_id, detail),
    ).fetchone()
    return int(row["calls"] if row else 0)


def _listing_operation_count(
    connection: sqlite3.Connection,
    run_id: str,
    detail: str,
) -> int:
    """Count an operation across every attempt for the run's submission.

    A listing may have up to three run attempts, but AGENTS.md section 7 grants
    one image-model call to the listing, not one to each attempt. Looking up the
    parent submission inside the same write transaction keeps a later retry
    from silently resetting that paid allowance.

    Raises:
        ValueError: When ``run_id`` does not identify a persisted listing run.
        sqlite3.Error: On a query failure.
    """
    listing = connection.execute(
        "SELECT response_row_id FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if listing is None:
        raise ValueError("a listing-scoped paid operation requires a known run id")
    row = connection.execute(
        "SELECT COUNT(*) AS calls FROM spend AS expense "
        "JOIN runs AS spent_run ON spent_run.run_id = expense.run_id "
        "WHERE spent_run.response_row_id = ? AND expense.note = ?",
        (str(listing["response_row_id"]), detail),
    ).fetchone()
    return int(row["calls"] if row else 0)


def record(connection: sqlite3.Connection, estimate: Estimate, run_id: str = "") -> None:
    """Log a paid call.

    Written even when the call failed: tokens burnt before an error still cost
    money, and a log that only records successes understates the bill.

    Args:
        connection: An open database connection.
        estimate: What it cost.
        run_id: The run it belongs to, if any.

    Raises:
        sqlite3.Error: on a write failure.
    """
    from gable.db import store

    store.record_spend(
        connection,
        service=estimate.service,
        run_id=run_id,
        model=estimate.model,
        units=estimate.usd,
        unit_kind="usd",
        note=estimate.detail,
    )


def _guard(connection: sqlite3.Connection, about_to_spend: float = 0.0) -> float:
    """Refuse a paid call that would cross the ceiling inside a reservation.

    Args:
        connection: An open database connection.
        about_to_spend: Estimated cost of the call being considered.

    Returns:
        The total spent so far, for logging.

    Raises:
        BudgetExceededError: when the ceiling would be crossed. Raising rather than
            returning a flag is deliberate: a caller can ignore a flag, and the
            whole point is that this cannot be ignored.
    """
    spent = total_spent(connection)
    if spent + about_to_spend >= CEILING_USD:
        msg = (
            f"stopping: this would take testing to ${spent + about_to_spend:.2f}, "
            f"past the ${CEILING_USD:.0f} ceiling"
        )
        raise BudgetExceededError(msg)
    return spent


def reserve(
    connection: sqlite3.Connection,
    estimate: Estimate,
    run_id: str = "",
    *,
    max_operations: int | None = None,
) -> None:
    """Atomically reserve one paid call before the vendor can be reached.

    ``BEGIN IMMEDIATE`` serializes the total, optional per-run operation count,
    and ledger insert across the poller and Slack event connections. A prior
    check-then-call-then-record sequence let two workers both observe available
    budget (or zero prior image edits), contact the vendor, and only afterwards
    record that they had crossed the limit.

    Args:
        connection: Database holding the cumulative spend ledger.
        estimate: Conservative cost to reserve.
        run_id: Associated flyer run when one is available.
        max_operations: Optional maximum reservations for this run's listing
            and exact estimate detail, across all of its attempts. ``None``
            applies only the shared dollar ceiling.

    Raises:
        BudgetExceededError: When this reservation would reach the shared ceiling.
        OperationLimitReachedError: When the per-run operation limit is exhausted.
        ValueError: When a limit or estimate is negative.
        sqlite3.Error: When the reservation cannot be committed.
    """
    if estimate.usd < 0:
        raise ValueError("a paid-call reservation cannot be negative")
    if max_operations is not None and max_operations < 0:
        raise ValueError("a paid-operation limit cannot be negative")

    connection.execute("BEGIN IMMEDIATE")
    try:
        if max_operations is not None:
            prior = _listing_operation_count(connection, run_id, estimate.detail)
            if prior >= max_operations:
                raise OperationLimitReachedError(
                    "this listing has already reserved its paid-operation allowance"
                )
        _guard(connection, estimate.usd)
        record(connection, estimate, run_id)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def guarded_call(
    connection: sqlite3.Connection,
    estimate: Estimate,
    call: Callable[[], _T],
    run_id: str = "",
    *,
    max_operations: int | None = None,
) -> _T:
    """Guard one paid operation and record its conservative reservation.

    The reservation is recorded even if the vendor fails after accepting the
    request, because a failed response can still be billable. Estimates are
    intentionally rounded up; stopping below Chase's ceiling is safer than
    reconstructing an invoice to the cent.

    Args:
        connection: Database holding the cumulative spend log.
        estimate: Conservative cost reserved for this operation.
        call: The paid operation. It is never invoked after the ceiling.
        run_id: Associated flyer run when one is available.
        max_operations: Optional per-listing limit for reservations carrying
            this estimate's exact detail marker, across every run attempt.

    Returns:
        The operation's return value.

    Raises:
        BudgetExceededError: Before ``call`` when the reservation reaches the
            ceiling.
        OperationLimitReachedError: Before ``call`` when the optional per-run
            allowance is exhausted.
        Exception: Any exception from ``call``, after the reservation is logged.
    """
    reserve(
        connection,
        estimate,
        run_id,
        max_operations=max_operations,
    )
    return call()


def summary(connection: sqlite3.Connection) -> str:
    """A one-line spend report, in plain words.

    Args:
        connection: An open database connection.

    Returns:
        Something safe to post in Slack.

    Raises:
        sqlite3.Error: on a query failure.
    """
    spent = total_spent(connection)
    headroom = max(0.0, CEILING_USD - spent)
    note = " Getting close to the limit." if spent >= WARN_AT_USD else ""
    return f"Testing has cost about ${spent:.2f} so far, ${headroom:.2f} left.{note}"
