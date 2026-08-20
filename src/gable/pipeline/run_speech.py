"""Persist and deliver what one run says, separately from deciding it.

Split out of `runner.py` when that file reached the 800-line ceiling. The seam
is deliberate rather than arithmetic: everything here is about getting an exact
sentence into the durable outbox and then into Slack, and nothing here decides
what the sentence should be. `runner.py` keeps the decisions.

Assumes the caller has already opened (or deliberately not opened) the listing
thread. Does not own retries -- the outbox worker does.
"""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from gable.listings.intake import Intake
from gable.pipeline import people
from gable.pipeline import questions as run_questions
from gable.pipeline.run_reporting import RunResult
from gable.voice import safe

#: Outcomes whose stored reason is the honest record of why a run stopped, and
#: so must survive into `runs.failure_reason` rather than being reset on
#: delivery. A delivered flyer has no such reason.
_REASONED_OUTCOMES: frozenset[str] = frozenset({"failed", "needs_review", "skipped"})


def deliver_outcome(
    connection: Connection,
    say: run_questions.Post,
    run_id: str,
    message: str,
    result: RunResult,
    *,
    status: str,
    detail: str,
    thread_ts: str = "",
    post_once: run_questions.PostOnce | None = None,
    reconcile: run_questions.ReconcilePost | None = None,
    pending_status: str | None = None,
    confirmation_detail: str = "Slack confirmed the run outcome",
    output_file_id: str = "",
    output_url: str = "",
) -> RunResult:
    """Persist and attempt one exact non-question outcome message.

    Args:
        connection: The open database.
        say: Posts a message and returns the thread timestamp it landed in.
        run_id: The run this outcome belongs to.
        message: The exact words, already in Gable's voice.
        result: The run record this mutates and returns.
        status: The state the run lands in once Slack confirms.
        detail: Immutable audit note for the transition.
        thread_ts: The listing thread, when one is already open.
        post_once: Durable single-delivery seam, when the caller has one.
        reconcile: Reconciles an ambiguous acknowledgement, when supplied.
        pending_status: The state to hold while delivery is unconfirmed.
        confirmation_detail: Audit note recorded on confirmation.
        output_file_id: The built flyer, when this outcome has one.
        output_url: The link to that flyer, when this outcome has one.

    Returns:
        The same `result`, with the delivered status, link and words recorded.

    Raises:
        sqlite3.Error: on a write failure.
    """
    delivered = run_questions.prepare_outcome_and_deliver(
        connection,
        run_id,
        message,
        status,
        say,
        post_once=post_once,
        reconcile=reconcile,
        pending_status=pending_status,
        thread_ts=thread_ts,
        confirmed_reason=detail if status in _REASONED_OUTCOMES else "",
        confirmation_detail=confirmation_detail,
        transition_detail=detail,
        output_file_id=output_file_id,
        output_url=output_url,
    )
    result.status = delivered.status
    result.output_url = output_url
    result.said.extend(delivered.said)
    return result


def deliver_question(
    connection: Connection,
    say: run_questions.Post,
    run_id: str,
    intake: Intake,
    question: str,
    questions: list[Any],
    result: RunResult,
    *,
    status: str = "needs_info",
    headline: str = "",
    thread_ts: str = "",
    post_once: run_questions.PostOnce | None = None,
    reconcile: run_questions.ReconcilePost | None = None,
) -> RunResult:
    """Persist one question, announce the listing, and confirm the pause.

    Args:
        connection: The open database.
        say: Posts a message and returns the thread timestamp it landed in.
        run_id: The run being paused.
        intake: The listing, for the announcement that opens its thread.
        question: The exact ask. An empty string becomes a safe generic one
            rather than an empty Slack message.
        questions: The remaining structured asks, recorded on the result.
        result: The run record this mutates and returns.
        status: The paused state this ask leaves the run in.
        headline: The announcement, when the caller has already built one.
        thread_ts: The listing thread, when one is already open.
        post_once: Durable single-delivery seam, when the caller has one.
        reconcile: Reconciles an ambiguous acknowledgement, when supplied.

    Returns:
        The same `result`, with the paused status and words recorded.

    Raises:
        sqlite3.Error: on a write failure.
    """
    asked = safe(question or "I need one more thing before I can build this.")
    opening = headline or people.opening_for(connection, intake, thread_ts)
    delivery = run_questions.prepare_and_deliver(
        connection,
        run_id,
        asked,
        status,
        say,
        post_once=post_once,
        reconcile=reconcile,
        headline=safe(opening) if opening else "",
        thread_ts=thread_ts,
    )
    result.status = delivery.status
    result.said.extend(delivery.said)
    if delivery.questions:
        result.questions = [asked, *[q.ask for q in questions[1:]]]
    return result
