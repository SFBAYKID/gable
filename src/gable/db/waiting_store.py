"""What each paused listing is still waiting on, for answering a person.

Separate from `question_store` because it asks the opposite question. That
module is an outbox: which message does Gable still owe Slack. This one is a
read: which listings still owe Gable something from a person, so "is this
built?" and "yes build it" can be answered wherever they are asked.

Split out on 2026-08-26 when `question_store.py` reached the 800-line ceiling.

Does not handle: writing anything. Every function here is a query.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from gable.db.run_store import PAUSED

#: How many waiting listings a person is told about by name. The live database
#: held 325 paused runs on 2026-08-26, most of them historical, so the answer to
#: "is this built?" cannot be a list of all of them -- `voice.safe` would trim it
#: to whichever two happened to sort first, which is worse than a count. Three
#: recent ones plus a total is an answer; a wall of stale rows is not.
NAMED_LIMIT: Final[int] = 3


@dataclass(frozen=True, slots=True)
class WaitingAsk:
    """One listing that is waiting on a person, and what it last asked for."""

    run_id: str
    listing: str
    thread_ts: str
    question: str


def waiting_ask_count(connection: sqlite3.Connection) -> int:
    """Return how many listings are waiting on a person right now.

    Args:
        connection: Open database connection.

    Returns:
        The count of paused runs that have asked something.

    Raises:
        sqlite3.Error: If the query cannot run.
    """
    row = connection.execute(
        f"""
        SELECT COUNT(DISTINCT q.run_id) AS waiting
          FROM run_questions q
          JOIN runs r ON r.run_id = q.run_id
         WHERE r.status IN ({",".join("?" * len(PAUSED))})
           AND q.question_label != ''
        """,
        tuple(sorted(PAUSED)),
    ).fetchone()
    return int(row["waiting"]) if row else 0


def waiting_asks(
    connection: sqlite3.Connection,
    limit: int = NAMED_LIMIT,
) -> tuple[WaitingAsk, ...]:
    """Return the most recently active paused listings and what each is owed.

    The listing is named from its own submission rather than from the question's
    headline, because only a run's FIRST question carries a headline and a run
    that has asked twice would otherwise be nameless.

    Args:
        connection: Open database connection.
        limit: How many to return, newest activity first.

    Returns:
        Up to `limit` entries, each naming the agent, the address, and the exact
        question that listing is still waiting on.

    Raises:
        sqlite3.Error: If the query cannot run.
    """
    rows = connection.execute(
        f"""
        SELECT r.run_id,
               s.agent_name,
               s.address,
               q.thread_ts,
               q.question_label
          FROM runs r
          JOIN submissions s ON s.response_row_id = r.response_row_id
          JOIN run_questions q ON q.run_id = r.run_id
         WHERE r.status IN ({",".join("?" * len(PAUSED))})
           AND q.question_label != ''
           AND q.created_at = (
               SELECT MAX(q2.created_at)
                 FROM run_questions q2
                WHERE q2.run_id = r.run_id
                  AND q2.question_label != ''
           )
         ORDER BY r.updated_at DESC
         LIMIT ?
        """,
        (*sorted(PAUSED), limit),
    ).fetchall()
    return tuple(
        WaitingAsk(
            run_id=str(row["run_id"]),
            listing=" — ".join(
                part for part in (str(row["agent_name"] or ""), str(row["address"] or "")) if part
            ),
            thread_ts=str(row["thread_ts"] or ""),
            question=str(row["question_label"] or ""),
        )
        for row in rows
    )
