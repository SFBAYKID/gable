"""Tests for the decision layer: what Gable does next, and why.

Every step is pure, so the whole sequence is checkable without Google, Slack or
a paid call. Fixtures come from real rows.
"""

from __future__ import annotations

import pytest

from gable.listings.enrich import Facts
from gable.listings.intake import Intake
from gable.pipeline.orchestrator import (
    Outcome,
    after_research,
    agent_slots,
    judge,
    plan,
)


def _intake(**overrides: str) -> Intake:
    base = {
        "agent_email": "lolo@cornerhouserealty.com",
        "agent_name": "Lolo Simmons",
        "request_type": "New Listing",
        "address": "7940 Oakwood Rd, Glen Burnie, MD 21061",
        "post_details": "",
        "open_house": "",
        "new_price": "",
        "closing_price": "",
        "extra_notes": "",
        "side": "",
        "notes": "",
    }
    base.update(overrides)
    return Intake(**base)


# --- the order of operations ------------------------------------------------


def test_a_contradiction_outranks_research() -> None:
    """No sense researching a property whose address is a review link."""
    step = plan(_intake(address="Google Review"))
    assert step.outcome is Outcome.ASK


def test_sold_with_no_closing_price_carries_on() -> None:
    """The price is offered after the link now, not demanded before the build."""
    step = plan(_intake(request_type="Sold"))
    assert step.outcome is not Outcome.ASK


def test_a_clean_row_goes_to_research_first() -> None:
    """Beds, baths and square footage are public. Look them up, do not ask."""
    step = plan(_intake())
    assert step.outcome is Outcome.RESEARCH
    assert "beds" in step.research
    assert step.category == "Just Listed"


def test_known_source_fields_scope_public_research() -> None:
    """An address-only source must not trigger a global listing-facts lookup."""
    step = plan(_intake(), required_public_facts=frozenset())
    assert step.outcome is Outcome.BUILD
    assert step.research == []


def test_known_source_researches_only_its_missing_public_field() -> None:
    step = plan(_intake(), required_public_facts=frozenset({"beds"}))
    assert step.outcome is Outcome.RESEARCH
    assert step.research == ["beds"]


def test_nothing_left_to_find_goes_straight_to_build() -> None:
    known = {
        "beds": "4",
        "baths": "3",
        "square_feet": "1,804",
        "list_price": "$515,000",
    }
    step = plan(_intake(closing_price="$515,000"), known)
    assert step.outcome is Outcome.BUILD


def test_cached_researched_price_prevents_another_lookup() -> None:
    """Facts.as_dict uses list_price, so the intake contract must use it too."""
    known = {
        "beds": "4",
        "baths": "3",
        "square_feet": "1,804",
        "list_price": "$515,000",
    }
    step = plan(_intake(), known)
    assert step.outcome is Outcome.BUILD


def test_a_request_type_the_old_catalogue_had_no_category_for_is_not_refused() -> None:
    """The design is whatever file carries this request type's name.

    Planning no longer decides whether one exists; the picker does, and it names
    the missing file rather than a category nobody outside the code has heard of.
    """
    step = plan(_intake(request_type="End of Year Brag Post"))
    assert step.outcome is not Outcome.SKIP


def test_an_unusable_address_is_not_researched() -> None:
    """A bad lookup wastes a paid call and returns confident nonsense."""
    step = plan(_intake(address="SRES Listing 29 Maple", request_type="Client Review Post"))
    assert step.outcome is not Outcome.RESEARCH


# --- what happens after a lookup --------------------------------------------


def test_good_research_leads_to_build_and_says_what_was_found() -> None:
    found = Facts(
        beds="4",
        baths="3",
        square_feet="1,804",
        list_price="$515,000",
        source_url="https://redfin.test",
        confidence=0.95,
    )
    step = after_research(_intake(), found, {})
    assert step.outcome is Outcome.BUILD
    assert "looked up" in step.say


def test_an_implausible_number_is_questioned_rather_than_used() -> None:
    """A confident wrong number on a flyer is worse than a blank."""
    found = Facts(
        beds="47",
        source_url="https://x.test",
        confidence=0.2,
        caveats=["the bedroom count I found (47) looks wrong"],
    )
    step = after_research(_intake(), found, {})
    assert step.outcome is Outcome.ASK
    assert "47" in step.say


def test_research_that_finds_nothing_asks() -> None:
    step = after_research(_intake(), Facts(), {})
    assert step.outcome is Outcome.ASK
    assert "could not find" in step.say


def test_missing_unselected_public_facts_do_not_become_questions() -> None:
    step = after_research(
        _intake(),
        Facts(beds="4", source_url="https://example.test", confidence=0.8),
        {},
        frozenset({"beds"}),
    )
    assert step.outcome is Outcome.BUILD
    assert "baths" not in step.say
    assert "square feet" not in step.say


def test_what_the_agent_typed_is_never_overwritten() -> None:
    """Gable fills blanks; it does not correct a human's own listing."""
    found = Facts(beds="9", baths="9", square_feet="9,999", confidence=0.9)
    step = after_research(_intake(), found, {"beds": "4", "baths": "3", "square_feet": "1,804"})
    assert step.outcome is Outcome.BUILD
    assert "beds" not in step.say


# --- the deterministic quality gate ----------------------------------------


def test_a_leftover_placeholder_fails_the_check() -> None:
    verdict = judge("Just Listed [PRICE] Lolo Simmons", {}, 1)
    assert verdict.passed is False
    assert "placeholder" in verdict.problems[0]


def test_a_value_that_never_landed_fails_the_check() -> None:
    verdict = judge("Just Listed 7940 Oakwood Rd", {"phone": "(443) 854-8554"}, 1)
    assert verdict.passed is False
    assert "phone did not make it" in verdict.problems[0]


def test_a_clean_render_passes() -> None:
    text = "Just Listed 7940 Oakwood Rd, Glen Burnie, MD 21061 Lolo Simmons (443) 854-8554"
    verdict = judge(text, {"address": "7940 Oakwood Rd", "phone": "(443) 854-8554"}, 1)
    assert verdict.passed is True
    assert verdict.say == ""


def test_a_failed_verdict_says_something_a_designer_can_act_on() -> None:
    verdict = judge("[PRICE]", {}, 2)
    assert verdict.say.startswith("I rendered it, but")
    assert "[" not in verdict.say


def test_the_second_pass_is_recorded_as_the_second() -> None:
    assert judge("clean", {}, 2).pass_number == 2


# --- one agent or two -------------------------------------------------------


def test_a_single_agent_listing_builds_a_single_agent_design() -> None:
    assert agent_slots(_intake()).outcome is Outcome.BUILD


def test_row_84_resolves_both_roles_without_asking() -> None:
    """Listed by Stacey, hosted by Jason. Both roles are explicit."""
    step = agent_slots(
        _intake(
            request_type="Open House",
            post_details="Listed by: Stacey Abbott. Hosted by: Jason Vetter",
        )
    )
    assert step.outcome is Outcome.BUILD
    assert "Stacey Abbott listing" in step.detail
    assert "Jason Vetter hosting" in step.detail


def test_two_agents_with_unclear_roles_are_asked_about() -> None:
    step = agent_slots(
        _intake(post_details="Listed by: Stacey Abbott", extra_notes="Co-listed by: Jason Vetter")
    )
    assert step.outcome is Outcome.ASK
    assert "listing agent" in step.say


def test_notes_that_imply_two_agents_without_roles_are_asked_about() -> None:
    step = agent_slots(_intake(notes="Please make this a two agent post."))
    assert step.outcome is Outcome.ASK
    assert "two-agent" in step.say
    assert "which" in step.questions[0].ask.lower()


@pytest.mark.parametrize("case", ["Sold", "Open House", "New Listing"])
def test_every_spoken_step_is_a_sentence_not_a_token(case: str) -> None:
    step = plan(_intake(request_type=case))
    if step.say:
        assert "[" not in step.say
        assert "{" not in step.say
