"""Scope paid property research to the fields on the selected source."""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection

from gable.db import store
from gable.listings.enrich import Facts, has_authoritative_source
from gable.listings.intake import Intake
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
) -> tuple[Step, dict[str, str]]:
    """Research only missing public fields displayed by this exact source."""
    required = required_public_facts(resolution, intake)
    # Existing rows predate address-identity proof and carry no durable marker
    # that distinguishes a matching property page from a plausible wrong search
    # result.  Keep them for audit, but do not trust them to fill a flyer.  A
    # selected gap is resolved by one current strict lookup or remains missing.
    # A person who answered Gable's question outranks anything on the web, and
    # is the reason the question was asked at all. Without this the answer was
    # acknowledged in Slack and then discarded: the run stayed at needs_info and
    # the next attempt asked for the same number again.
    known = {
        field: value
        for field, value in store.recall_supplied_facts(connection, intake.address).items()
        if field in required
    }
    step = plan(intake, known, required)
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
    return after_research(intake, found, known, required), known
