"""Scope paid property research to the fields on the selected source."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from sqlite3 import Connection

from gable.db import store
from gable.listings.enrich import Facts, has_authoritative_source
from gable.listings.intake import Intake
from gable.pipeline.needs import internal_name
from gable.pipeline.orchestrator import Outcome, Step, after_research, plan
from gable.slides.fields import Resolution


def required_public_facts(resolution: Resolution, intake: Intake) -> frozenset[str]:
    """Return web-researchable facts represented by this request and source."""
    required = frozenset({"beds", "baths", "square_feet"} & resolution.fields.keys())
    # Intake and Facts use different truthful names for the same source field:
    # the selected design says price, while cached research calls it list_price.
    # A Sold closing price and a Price Reduction's new price are not public list
    # prices, so neither may be filled from this web path.
    if "price" in resolution.fields and intake.accepts_public_list_price:
        required |= frozenset({"list_price"})
    return required


def resolve(
    connection: Connection,
    intake: Intake,
    resolution: Resolution,
    research: Callable[[str, frozenset[str]], Facts],
    progress: Callable[[], None] = lambda: None,
    allow_blank_fields: bool = False,
) -> tuple[Step, dict[str, str]]:
    """Research only missing public fields displayed by this exact source.

    `allow_blank_fields` is set once a person has said to build without the
    values Gable could not find. Research still runs — a fact that can be found
    is better than a blank — but a remaining gap no longer stops the run.
    """
    required = required_public_facts(resolution, intake)
    # Existing rows predate address-identity proof and carry no durable marker
    # that distinguishes a matching property page from a plausible wrong search
    # result.  Keep them for audit, but do not trust them to fill a flyer.  A
    # selected gap is resolved by one current strict lookup or remains missing.
    # A person who answered Gable's question outranks anything on the web, and
    # is the reason the question was asked at all. Without this the answer was
    # acknowledged in Slack and then discarded: the run stayed at needs_info and
    # the next attempt asked for the same number again.
    supplied = store.recall_supplied_facts(connection, intake.address)
    known = {field: value for field, value in supplied.items() if field in required}
    step = _without_answered(plan(intake, known, required), supplied)
    if step.outcome is not Outcome.RESEARCH:
        return step, known

    progress()
    found = research(intake.address, required)
    if not has_authoritative_source(found):
        found = Facts()
    if not found.is_empty:
        store.remember_facts(
            connection,
            intake.address,
            found.as_dict(),
            found.source_url,
            found.confidence,
        )
        # A stated fact still wins. Research runs whenever any required field is
        # missing, so a lookup triggered by an absent square footage must not
        # replace the price a person just gave with whatever a listing page says.
        known = {**found.as_dict(), **known}
    step = _without_answered(after_research(intake, found, known, required), supplied)
    if allow_blank_fields and step.outcome is Outcome.ASK:
        # The gap was already put to a person and they chose to proceed. Asking
        # again with the same words is how a question becomes a dead end. The
        # names are carried through so the delivery message can say which
        # values were left as placeholders.
        return Step(outcome=Outcome.BUILD, missing=step.missing), known
    return step, known


def _without_answered(step: Step, supplied: Mapping[str, str]) -> Step:
    """Drop the questions somebody has already answered from a step.

    `plan` re-runs the coherence check, which reads the form's own columns and
    knows nothing about what anyone supplied. Gable asked Jay Hinish's listing
    for its open house date and time, was given it, and asked again — the first
    ask was filtered and this second one was not.

    Args:
        step: What the planner decided.
        supplied: Every value stated for this property, as stored.

    Returns:
        The same step with answered questions removed, or a BUILD when nothing
        is left to ask. Any other outcome is returned untouched.

    Raises:
        Nothing.
    """
    if step.outcome is not Outcome.ASK or not supplied:
        return step
    keep = [
        question
        for question in step.questions
        if not supplied.get(internal_name(question.field_name), "").strip()
    ]
    if len(keep) == len(step.questions):
        return step
    if not keep:
        built: Step = replace(step, outcome=Outcome.BUILD, questions=[], missing=(), say="")
        return built
    narrowed: Step = replace(
        step,
        questions=keep,
        say=keep[0].ask,
        missing=tuple(q.field_name for q in keep if q.absent),
    )
    return narrowed
