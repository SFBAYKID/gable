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

from gable.db.run_store import PAUSED


@dataclass(frozen=True, slots=True)
class WaitingAsk:
    """One listing that is waiting on a person, and what it last asked for."""

    run_id: str
    headline: str
    thread_ts: str
    question: str


def waiting_asks(connection: sqlite3.Connection) -> tuple[WaitingAsk, ...]:
    """Return every paused listing with the question it is still owed.

    "Build it" said outside a listing thread is a real instruction with no
    listing attached. Answering it needs the listings that are actually
    waiting, which is cheaper and more useful than re-measuring the design
    folder -- Chase said exactly that on 2026-08-26 and got a template report.

    Args:
        connection: Open database connection.

    Returns:
        One entry per paused run that has asked something, oldest first. The
        headline is the run's first, which is the one naming the property; the
        question is its most recent.

    Raises:
        sqlite3.Error: If the query cannot run.
    """
    rows = connection.execute(
        f"""
        SELECT q.run_id, q.headline, q.thread_ts, q.question_label, q.created_at
          FROM run_questions q
          JOIN runs r ON r.run_id = q.run_id
         WHERE r.status IN ({",".join("?" * len(PAUSED))})
         ORDER BY q.created_at
        """,
        tuple(sorted(PAUSED)),
    ).fetchall()
    headlines: dict[str, str] = {}
    latest: dict[str, WaitingAsk] = {}
    for row in rows:
        run_id = str(row["run_id"])
        headline = str(row["headline"] or "")
        if headline and run_id not in headlines:
            headlines[run_id] = headline
        question = str(row["question_label"] or "")
        if question:
            latest[run_id] = WaitingAsk(
                run_id=run_id,
                headline=headlines.get(run_id, ""),
                thread_ts=str(row["thread_ts"] or ""),
                question=question,
            )
    return tuple(latest[run_id] for run_id in latest)
