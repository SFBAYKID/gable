"""Scope paid property research to the fields on the selected source."""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection

from gable.db import store
from gable.listings.enrich import Facts
from gable.listings.intake import Intake
from gable.pipeline.orchestrator import Outcome, Step, after_research, plan
from gable.slides.fields import Resolution


def required_public_facts(resolution: Resolution) -> frozenset[str]:
    """Return cached fact names represented by the source's resolved fields."""
    required = frozenset({"beds", "baths", "square_feet"} & resolution.fields.keys())
    # Intake and Facts use different truthful names for the same source field:
    # the selected design says price, while cached research calls it list_price.
    if "price" in resolution.fields:
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
    required = required_public_facts(resolution)
    known = store.recall_facts(connection, intake.address)
    step = plan(intake, known, required)
    if step.outcome is not Outcome.RESEARCH:
        return step, known

    progress()
    found = research(intake.address, required)
    if not found.is_empty:
        store.remember_facts(
            connection,
            intake.address,
            found.as_dict(),
            found.source_url,
            found.confidence,
        )
        known = store.recall_facts(connection, intake.address)
    return after_research(intake, found, known, required), known
