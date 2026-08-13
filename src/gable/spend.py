"""What testing costs, and the ceiling it must not cross.

Chase set a hard limit: **stop at $50.** An agent that can call a paid API in a
loop needs that limit in code, not in a comment (AGENTS.md §7), so `guard()` is
consulted before every paid call and raises rather than returning a value the
caller might ignore.

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


def guard(connection: sqlite3.Connection, about_to_spend: float = 0.0) -> float:
    """Refuse a paid call that would cross the ceiling.

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


def guarded_call(
    connection: sqlite3.Connection,
    estimate: Estimate,
    call: Callable[[], _T],
    run_id: str = "",
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

    Returns:
        The operation's return value.

    Raises:
        BudgetExceededError: before ``call`` when the reservation reaches the
            ceiling.
        Exception: any exception from ``call``, after the reservation is logged.
    """
    guard(connection, estimate.usd)
    try:
        return call()
    finally:
        record(connection, estimate, run_id)


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
