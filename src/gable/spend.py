"""What testing costs, and the ceiling it must not cross.

Chase set a hard limit: **stop at $50.** An agent that can call a paid API in a
loop needs that limit in code, not in a comment (AGENTS.md §7), so `guard()` is
consulted before every paid call and raises rather than returning a value the
caller might ignore.

Prices are per million tokens, from each vendor's own page on 2026-08-11. They
are estimates for a running total, not an invoice — the authority is the vendor's
dashboard, and this exists to stop a runaway, not to do accounting.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, TypeVar

#: USD per million tokens, input and output.
TOKEN_PRICES: Final[dict[str, tuple[float, float]]] = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5": (1.25, 10.00),
    "gpt-image-1-mini": (2.50, 8.00),
    "gpt-image-2": (8.00, 30.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}

#: Firecrawl bills per search rather than per token.
FIRECRAWL_PER_SEARCH: Final[float] = 0.01

#: Conservative reservations for calls whose actual token count is not known
#: until after the vendor replies. Both exceed the configured maximum-output
#: cost plus the normal prompt, so the guard stops early rather than late.
CONVERSATION_RESERVE_USD: Final[float] = 0.01
VISION_RESERVE_USD: Final[float] = 0.01

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


def token_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the cost of one model call.

    Args:
        model: The model name.
        input_tokens: Prompt tokens.
        output_tokens: Completion tokens.

    Returns:
        Cost in USD. An unknown model is priced at the most expensive known
        rate, so an unrecognised name cannot hide spending.

    Raises:
        Nothing.
    """
    if model in TOKEN_PRICES:
        per_in, per_out = TOKEN_PRICES[model]
    else:
        per_in, per_out = max(TOKEN_PRICES.values(), key=lambda pair: pair[1])
    return (input_tokens * per_in + output_tokens * per_out) / 1_000_000


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
