"""Every answer a person can give has to reach the values a flyer is built from.

This is the invariant two separate #calvo threads died on. Chase, 2026-09-21,
after reading them: "make sure this mistake doesn't happen."

The failure is always the same shape and it is invisible from the outside.
Gable stops and asks. The person answers. `supply_listing_value` records the
answer in `supplied_facts`, exactly as designed, and reports success. Then
`run_values.for_intake` declines to carry it onto the flyer for some local
reason, so the check that made Gable stop is still unsatisfied and the same
question goes out again. Nothing errors, nothing logs, and the thread reads as
though Gable is not listening.

It has happened three times now, each time for a different local reason:

- 2026-08-14, `list_price`: a stated price landed in the table and was
  discarded, so "if you give me the price I can add it" was a dead end.
- 2026-08-20, `open_house`: Jay Hinish's listing was given "Saturday, Aug 22,
  2026 1-3PM" and asked again.
- 2026-09-21, `review_quote`: accepted only when a `client_name` was already
  known, which on Client Review Post can never be true at the moment the
  question is asked, because that design resolves the quote first.

So this is written against `store.SUPPLIABLE_FIELDS` rather than against the
three fields that happened to break. A field added to the enum a person can
answer with, and then not carried, fails here.

Does not handle: whether the value is CORRECT, or whether a form column should
outrank it. Those are `test_run_values`' business. This asks only that stating
something changes what gets built, because a stated value that changes nothing
is a question with no answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.pipeline.run_values import for_intake

#: One plausible answer per suppliable field, and the values key it must reach.
#: `list_price` is the odd one: the form column outranks it by design, so the
#: fixture below leaves the price column empty, which is the state a person is
#: ever asked about.
ANSWERS: dict[str, tuple[str, str]] = {
    "beds": ("4", "beds"),
    "baths": ("2.5", "baths"),
    "square_feet": ("2,430", "square_feet"),
    "list_price": ("$600,000", "price"),
    "open_house": ("Saturday, Sep 26, 2026 1-3PM", "open_house"),
    "review_quote": ("Ian made our first purchase painless and never rushed us.", "review_quote"),
    "client_name": ("Sharon", "client_name"),
}

#: A review field only reaches a design that shows one, so it is exercised
#: against the request type that draws it.
REVIEW_FIELDS: frozenset[str] = frozenset({"review_quote", "client_name"})


def _intake(request_type: str) -> Intake:
    """A submission whose every suppliable column is blank, so nothing masks."""
    return Intake(
        agent_email="agent@example.com",
        agent_name="Avery Agent",
        request_type=request_type,
        address="1 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )


def test_the_answers_table_covers_every_field_a_person_may_supply() -> None:
    """Otherwise a new suppliable field is silently exempt from the rule."""
    assert set(ANSWERS) == set(store.SUPPLIABLE_FIELDS)


@pytest.mark.parametrize("field_name", sorted(ANSWERS))
def test_a_stated_answer_changes_what_the_flyer_is_built_from(
    field_name: str,
    tmp_path: Path,
) -> None:
    """Record one answer, alone, and it must reach the value map.

    Alone is the point. Every regression so far was a condition on some OTHER
    value being present first, and a person answering the one question Gable
    asked has only ever supplied the one thing.
    """
    stated, key = ANSWERS[field_name]
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = _intake("Client Review Post" if field_name in REVIEW_FIELDS else "Open House")

    before = for_intake(connection, intake, {})
    store.remember_supplied_fact(connection, intake.address, field_name, stated)
    after = for_intake(connection, intake, {})

    assert not before.get(key, ""), f"{field_name} was already set, so this proves nothing"
    assert after.get(key, "") == stated, (
        f"a person answered with {field_name} and the flyer was built without it"
    )
    connection.close()
