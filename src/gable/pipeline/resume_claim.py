"""Deciding whether one resume attempt owns a paused run.

Two things can try to continue the same listing at once: Slack delivering the
same upload twice, a startup recovery pass and a live handler, or a person
answering while a poller is already working. Exactly one may proceed, and the
decision has to be a single atomic database claim rather than a status read
followed by a write.

This module holds that decision and nothing else, so the runner keeps only the
work. Pure apart from the store calls it is handed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Final

from gable.db import store
from gable.pipeline import questions as run_questions

logger = logging.getLogger("gable.resume")

#: The paused states a Slack photo upload may resume. `needs_photo` is a run
#: that asked for a photograph; `needs_review` is one whose flyer was built and
#: withheld, which is where the visual gate parks a photo it will not accept;
#: `needs_info` is one stopped on a preflight blocker, which now asks for the
#: photograph in the same breath and so must be able to take it. All three hold
#: an unsent draft, so a replacement image is welcome in any of them, and a run
#: that refused one was a dead end — the only thing that fixes it was the only
#: thing it would not take.
PHOTO_RESUME_STATES: Final[frozenset[str]] = frozenset(
    {"needs_photo", "needs_review", "needs_info"}
)

#: Said when another worker owns the run. Deliberately not said when the loser
#: is a duplicate delivery of the same upload; see `Claim.duplicate`.
ALREADY_WORKING: Final[str] = (
    "This listing is already being rechecked or is no longer waiting, so I "
    "did not start another copy of the same work."
)


@dataclass(frozen=True, slots=True)
class Claim:
    """The outcome of one attempt to own a paused run."""

    #: True when this caller may proceed with the work.
    won: bool
    #: The run's status now, for a caller that must report rather than build.
    status: str = "failed"
    #: True when losing means this is the second delivery of one upload. The
    #: winner is building the same thing, so saying anything would put a
    #: contradiction beside the ready link it is about to post.
    duplicate: bool = False


def claim_for_resume(
    connection: Connection,
    run_id: str,
    fields: dict[str, str | int],
    *,
    expected_status: str | None,
    origin_thread_ts: str,
    photo_event_id: str = "",
) -> Claim:
    """Take ownership of a paused run, or explain why this caller may not.

    Args:
        connection: The open database.
        run_id: The paused run being continued.
        fields: Columns committed atomically with the claim, such as photo
            provenance. Written only if the claim succeeds.
        expected_status: The exact paused state the caller saw. None accepts
            any paused state.
        origin_thread_ts: The owned Slack thread, when the resume came from one.
        photo_event_id: The Slack file event this resume carries, if any.

    Returns:
        A `Claim`. `won` is the only field a successful caller needs.

    Raises:
        Nothing. A failure to claim is an outcome, not an error.
    """
    if expected_status in PHOTO_RESUME_STATES and origin_thread_ts:
        # The photo path also resolves the question the upload answers, so it
        # runs under the same guard the question outbox uses.
        with run_questions.run_question_guard(run_id):
            claimed = store.claim_run_for_photo(
                connection,
                run_id,
                origin_thread_ts,
                fields,
                expected_status=expected_status or "needs_photo",
            )
    else:
        claimed = store.claim_paused_run(
            connection,
            run_id,
            fields,
            expected_status=expected_status,
        )
    if claimed:
        return Claim(won=True, status="pending")
    current = store.run_by_id(connection, run_id)
    status = current.status if current is not None else "failed"
    duplicate = bool(
        expected_status in PHOTO_RESUME_STATES
        and photo_event_id
        and current is not None
        and current.photo_event_id == photo_event_id
    )
    if duplicate:
        logger.info("run %s was already claimed by the same upload", run_id)
    return Claim(won=False, status=status, duplicate=duplicate)
