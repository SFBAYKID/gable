"""Tests for reading the eleven columns that matter.

Fixtures are real rows from the live sheet, not invented ones. Row 84 in
particular earns its place: it is the dual-agent open house that proved the
second agent arrives as prose in the details column.
"""

from __future__ import annotations

import pytest

from gable.listings.intake import (
    COLUMNS,
    Intake,
    address_looks_usable,
    context_text,
    from_row,
    incoherences,
    mentions_multiple_agents,
    missing_public_facts,
    named_agents,
    needs_two_agents,
    price_note,
)

# Row 84, exactly as the sheet returns it.
ROW_84 = [
    "8/5/2026 9:12:03",
    "jason@cornerhouserealty.com",
    "Jason Vetter",
    "I understand that requests are fulfilled Monday through Friday...",
    "Open House",
    "",
    "",
    "",
    "",
    "",
    "Static Instagram/Facebook Post",
    "3 Nob Hill Park Dr, Reisterstown, MD 21136",
    "",
    "Listed by: Stacey Abbott. Hosted by: Jason Vetter",
    "August 8th & 9th 12:00-2:00",
]


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


# --- the column map ---------------------------------------------------------


def test_only_the_eleven_columns_chase_named_are_read() -> None:
    assert set(COLUMNS) == {"B", "C", "E", "L", "N", "O", "P", "Q", "R", "S", "T"}


def test_column_k_is_deliberately_absent() -> None:
    """Social media content type is out of scope; Gable makes flyers."""
    assert "K" not in COLUMNS


def test_row_84_reads_correctly() -> None:
    i = from_row(ROW_84)
    assert i.agent_email == "jason@cornerhouserealty.com"
    assert i.agent_name == "Jason Vetter"
    assert i.request_type == "Open House"
    assert i.address == "3 Nob Hill Park Dr, Reisterstown, MD 21136"
    assert i.open_house == "August 8th & 9th 12:00-2:00"


def test_a_short_row_does_not_raise() -> None:
    """Sheets truncates trailing empties; one bad row must not stop a batch."""
    i = from_row(["ts", "a@b.com", "A B"])
    assert i.address == ""
    assert i.notes == ""


def test_an_empty_row_does_not_raise() -> None:
    assert from_row([]).agent_email == ""


def test_email_is_lowercased_for_the_join() -> None:
    assert from_row(["ts", "LOLO@Corner.COM"]).agent_email == "lolo@corner.com"


# --- column E routes to a template category ---------------------------------


@pytest.mark.parametrize(
    ("request_type", "expected"),
    [
        ("Open House", "Open House"),
        ("Sold", "Just Sold"),
        ("Under Contract", "Under Contract"),
        ("New Listing", "Just Listed"),
        ("just listed", "Just Listed"),  # the one lower-case row on the sheet
        ("Client Review Post", "Client Review"),
        ("Price Reduction", "Just Listed"),
    ],
)
def test_request_type_routes_to_a_category(request_type: str, expected: str) -> None:
    assert _intake(request_type=request_type).category == expected


def test_an_unknown_request_type_routes_nowhere_rather_than_guessing() -> None:
    """'End of Year Brag Post' has no design. Picking a near one would be wrong."""
    assert _intake(request_type="End of Year Brag Post").category == ""
    assert _intake(request_type="something new").category == ""


# --- price comes from whichever column the type populates -------------------


def test_a_sold_post_takes_the_closing_price() -> None:
    assert _intake(request_type="Sold", closing_price="$450,000").price == "$450,000"


def test_a_reduction_takes_the_new_price() -> None:
    assert _intake(request_type="Price Reduction", new_price="$345,000").price == "$345,000"


def test_no_price_column_means_no_price() -> None:
    assert _intake().price == ""


# --- public facts are researched, never asked -------------------------------


def test_beds_baths_and_sqft_are_researched_not_asked() -> None:
    """An address establishes these. Making Carmen type them is the work we remove."""
    facts = missing_public_facts(_intake())
    assert "beds" in facts
    assert "baths" in facts
    assert "square_feet" in facts


def test_a_known_fact_is_not_researched_twice() -> None:
    facts = missing_public_facts(_intake(), known={"beds": "4", "baths": "3"})
    assert "beds" not in facts
    assert "square_feet" in facts


def test_a_supplied_price_is_not_researched() -> None:
    assert "price" not in missing_public_facts(_intake(closing_price="$450,000"))


def test_nothing_is_researched_without_an_address() -> None:
    assert missing_public_facts(_intake(address="")) == []


# --- coherence: contradictions get a question -------------------------------


def test_sold_without_a_closing_price_no_longer_stops_the_build() -> None:
    """Chase's rule, 2026-08-12: build it, then offer to add the price.

    This used to be a blocking question. A flyer with a photo, an agent, an
    address and a design should not wait on a number that can be typed into the
    thread in two seconds after the link arrives.
    """
    assert incoherences(_intake(request_type="Sold")) == []
    note = price_note(_intake(request_type="Sold"), design_shows_a_price=True)
    assert "no price" in note.lower()
    assert "give me the price" in note.lower()


def test_a_sold_post_with_a_price_says_nothing_afterwards() -> None:
    assert price_note(_intake(request_type="Sold", closing_price="$450,000"), True) == ""


def test_a_design_with_no_price_slot_is_not_offered_a_price() -> None:
    """The live `Sold` design carries an address and an agent card, no price.

    Offering to add one there promises something that cannot be done, and the
    next message has to take it back.
    """
    assert price_note(_intake(request_type="Sold"), design_shows_a_price=False) == ""


def test_sold_with_a_closing_price_is_not_asked_about() -> None:
    assert incoherences(_intake(request_type="Sold", closing_price="$450,000")) == []


def test_a_price_reduction_with_no_new_price_is_asked_about() -> None:
    asks = incoherences(_intake(request_type="Price Reduction"))
    assert any(q.field_name == "new price" for q in asks)


def test_an_open_house_with_no_time_is_asked_about() -> None:
    asks = incoherences(_intake(request_type="Open House"))
    assert any("date and time" in q.field_name for q in asks)


def test_row_84_needs_nothing_asked() -> None:
    """A complete row must produce no questions, or Gable becomes noise."""
    assert incoherences(from_row(ROW_84)) == []


def test_a_missing_address_is_asked_about_first() -> None:
    asks = incoherences(_intake(address=""))
    assert asks[0].field_name == "address"


def test_two_prices_at_once_is_a_contradiction() -> None:
    asks = incoherences(_intake(new_price="$1", closing_price="$2"))
    assert any(q.field_name == "price" for q in asks)


def test_a_request_type_with_no_category_is_not_a_contradiction() -> None:
    """Whether a design exists is the picker's business, not this module's.

    A template is now whatever file in Generic Templates carries this request
    type's name, so "End of Year Brag Post" builds the moment Carmen files one
    and stops with an actionable sentence until she does.
    """
    assert incoherences(_intake(request_type="End of Year Brag Post")) == []


def test_every_question_is_a_sentence_a_designer_can_answer() -> None:
    for case in ("Sold", "Open House", "Price Reduction"):
        for q in incoherences(_intake(request_type=case)):
            assert q.ask.endswith("?")
            assert "[" not in q.ask and "{" not in q.ask


# --- the dual-agent case, discovered on row 84 ------------------------------


def test_row_84_names_two_agents_in_the_details_column() -> None:
    """The second agent is prose in column N, not a missing form field."""
    roles = named_agents(from_row(ROW_84))
    assert roles["listing"] == "Stacey Abbott"
    assert roles["hosting"] == "Jason Vetter"


def test_row_84_wants_a_dual_agent_design() -> None:
    assert needs_two_agents(from_row(ROW_84)) is True


def test_the_submitter_is_not_assumed_to_be_the_listing_agent() -> None:
    """On row 84 the submitter holds the HOSTING role, not the listing.

    Putting the submitter in the listing slot would look perfectly correct on
    the flyer and be wrong.
    """
    intake = from_row(ROW_84)
    assert intake.agent_name == "Jason Vetter"
    assert named_agents(intake)["listing"] != intake.agent_name


def test_one_named_agent_is_not_a_dual_agent_post() -> None:
    assert needs_two_agents(_intake(post_details="Listed by: Lolo Simmons")) is False


def test_no_named_agents_is_the_common_case() -> None:
    assert named_agents(_intake()) == {}
    assert needs_two_agents(_intake()) is False


def test_template_context_includes_every_notes_section() -> None:
    intake = _intake(
        post_details="post detail",
        open_house="Saturday",
        extra_notes="extra note",
        side="buyer side",
        notes="final note",
    )
    text = context_text(intake)
    for phrase in ("post detail", "Saturday", "extra note", "buyer side", "final note"):
        assert phrase in text


def test_multiple_agents_can_be_flagged_before_both_names_are_known() -> None:
    intake = _intake(notes="This needs to be a two agent post.")
    assert named_agents(intake) == {}
    assert mentions_multiple_agents(intake) is True


# --- addresses that are not addresses ---------------------------------------


def test_a_real_address_is_usable() -> None:
    assert address_looks_usable("7940 Oakwood Rd, Glen Burnie, MD 21061") is True


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "  ",
        "Google Review",
        "https://g.page/r/abc",
        "SRES Listing 29 Maple",  # a real value from the sheet, and not an address
        "n/a",
        "123 Main Street",  # no state or ZIP, so nothing can be geocoded
    ],
)
def test_a_non_address_is_not_sent_to_a_lookup(bad: str) -> None:
    """A bad lookup wastes a paid call and returns confident nonsense."""
    assert address_looks_usable(bad) is False


@pytest.mark.parametrize(
    "good",
    [
        "7940 Oakwood Rd, Glen Burnie, MD 21061",
        "3 Nob Hill Park Dr, Reisterstown, MD 21136",
        "32 S Prospect Ave Baltimore, MD 21228",
        "620 S Wolfe St, Baltimore MD",
    ],
)
def test_every_real_address_on_the_sheet_is_usable(good: str) -> None:
    """Tightening the check must not start rejecting genuine addresses."""
    assert address_looks_usable(good) is True
