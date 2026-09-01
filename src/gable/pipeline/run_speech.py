"""Persist and deliver what one run says, separately from deciding it.

Split out of `runner.py` when that file reached the 800-line ceiling. The seam
is deliberate rather than arithmetic: everything here is about getting an exact
sentence into the durable outbox and then into Slack, and nothing here decides
what the sentence should be. `runner.py` keeps the decisions.

Assumes the caller has already opened (or deliberately not opened) the listing
thread. Does not own retries -- the outbox worker does.
"""

from __future__ import annotations

import logging
from sqlite3 import Connection
from typing import Any

from gable.db import store
from gable.listings.intake import Intake
from gable.pipeline import needs, people
from gable.pipeline import questions as run_questions
from gable.pipeline.run_reporting import RunResult
from gable.voice import MAX_ASK_CHARS, safe

logger = logging.getLogger("gable.runner")

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
    # The ask is a report, not a reply -- see `voice.MAX_ASK_CHARS`. Trimmed at
    # the reply ceiling it dropped the photo request itself.
    asked = safe(question or "I need one more thing before I can build this.", MAX_ASK_CHARS)
    guarded = repeat_guard(store.confirmed_questions_for_run(connection, run_id), asked)
    if guarded is None:
        # Asked, escalated, and about to be asked a third time: say nothing,
        # stay paused, and leave the thread readable.
        logger.error("run %s would ask the same question a third time; staying quiet", run_id)
        store.set_status(
            connection,
            run_id,
            status,
            "the same question was already asked and escalated; not repeating it",
            failure_reason=asked,
        )
        result.status = status
        return result
    if guarded != asked:
        logger.error("run %s was about to ask the same question again; escalating", run_id)
    asked = guarded
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


#: How a repeated question is escalated. The question travels with it so the
#: thread still says what is wrong, and the last sentence is what changes: a
#: person reading it knows Gable will not keep asking.
STUCK_OPENING: str = "I have your reply, and I am still stuck on the same thing."
STUCK_CLOSING: str = "I will not ask again in this thread. Chase, this one needs you."


def _folded(text: str) -> str:
    """One sentence compared as words, not bytes."""
    return " ".join(text.split()).casefold()


def repeat_guard(already_asked: tuple[str, ...], asked: str) -> str | None:
    """Stop a run saying the same sentence to the same thread twice.

    On 2026-09-01 Lina Mariner's thread got "The address reads ..., which looks
    like more than one property. Which one is this post for?" three times,
    each time after Carmen had answered it. The repeat is the failure Carmen
    sees as "not listening", whatever the cause underneath, and every false
    positive from now on costs one round instead of four.

    Args:
        already_asked: Every question this run has already put in front of a
            person, oldest first.
        asked: The question about to go out.

    Returns:
        `asked` unchanged when it is new; the escalation carrying it when it
        has been asked before; None when the escalation has also already been
        sent, in which case the caller says nothing and stays paused.

    Raises:
        Nothing.
    """
    seen = [_folded(item) for item in already_asked]
    if _folded(asked) not in seen:
        return asked
    escalation = safe(f"{STUCK_OPENING} {asked} {STUCK_CLOSING}", MAX_ASK_CHARS)
    if _folded(escalation) in seen:
        return None
    return escalation


def record_the_ask(connection: Connection, run_id: str, outstanding: needs.Needs) -> str:
    """Write down what the batched ask commits to, then return its exact words.

    Built once and checked, so what is RECORDED follows what actually goes out.
    Recording from the intended text let a 1004-character ask be trimmed to 548
    -- losing both "Separately, can you send me the property photo?" and the
    sentence that makes silence a usable answer -- while the run recorded that
    it had asked for a photograph and approved building with the values blank.
    Carmen would have seen three paragraphs about template widths, nothing she
    could send, and a listing that then waited on her forever.

    Args:
        connection: The open database.
        run_id: The run about to ask.
        outstanding: Everything this run still needs.

    Returns:
        The exact message, already trimmed to the report ceiling, for the
        caller to deliver unchanged.

    Raises:
        sqlite3.Error: on a write failure.
    """
    asked = safe(outstanding.message(), MAX_ASK_CHARS)
    if outstanding.values:
        if needs.LEAVE_OUT_MARK in asked:
            # Saying so is what makes one round enough: from here, silence is
            # an answer and an unsupplied value keeps its placeholder.
            store.approve_blank_fields(
                connection,
                run_id,
                "asked for every outstanding value and the photo in one message",
            )
        else:
            # Releasing off a sentence Carmen never saw is how a value stops
            # being asked for and never reaches a flyer.
            logger.error("run %s asked for values without the sentence that releases them", run_id)
    # Recorded before the ask goes out, because the status it parks in cannot
    # carry it: a blocker owns `status` -- it names the work only a person can
    # do outside Slack -- and the photo request rides in the same message.
    # Without this the upload answering it is refused; see `set_awaiting_photo`.
    store.set_awaiting_photo(connection, run_id, outstanding.photo)
    named = needs.PHOTO_ONLY_ASK in asked or needs.PHOTO_ASK_BESIDE_A_BLOCKER in asked
    if outstanding.photo and not named:
        logger.error("run %s recorded a photo ask that its message does not carry", run_id)
    return asked
