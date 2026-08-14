"""The one write-scoped tool, and the fences that keep it harmless."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from gable.agents.contacts import Contact

seed: Any = importlib.import_module("tools.seed_test_rows")

#: Testing_1's own header, which is not the live form's column order. Reading
#: it by position instead of by name would put the address in the notes.
TESTING_HEADER: list[str] = [
    "r",
    "Email Address",
    "First Name",
    "Second Name",
    "Service Guidelines Acknowledgment",
    "Select your request type",
    "Please provide the property address for the postcard",
    "Select postcard category",
    "Upload photos",
    "Upload your video assets (For Video Editing Requests Only)",
    "Optional: Include any details/instruction for your video",
    "Select social media content type",
    "Property Address",
    "Upload high-resolution property photos (up to 5 images)",
    "Include details for post - required for Client Review post",
    "Open house date/time (if applicable)",
    "New price (if price improvement)",
    "Closing price (for sold posts only):",
    "Additional Notes for Social Media Team",
    "For Sold or Under Contract posts, were you on the buyer or seller side?",
    "Notes",
]

ANDY = Contact(email="andy@cornerhouserealty.com", first_name="Andy", last_name="Jang", phone="1")
KELSEY_A = Contact(
    email="kelsey@cornerhouserealty.com", first_name="Kelsey", last_name="Mahon", phone="2"
)
KELSEY_B = Contact(
    email="kelsey@kelseysellshomes.com", first_name="Kelsey", last_name="Mahon", phone="3"
)


@pytest.mark.parametrize("tab", ["Form Responses 1", "form responses 1", "Sheet1", " Responses "])
def test_only_a_testing_tab_may_be_written(tab: str) -> None:
    """The whole safety of the tool. The live form tab is never a legal target."""
    with pytest.raises(seed.SeedError, match="refusing to write"):
        seed.check_tab(tab)


@pytest.mark.parametrize("tab", ["Testing_1", "Testing_2", " testing_scratch "])
def test_a_testing_tab_is_accepted(tab: str) -> None:
    assert seed.check_tab(tab) == tab.strip()


def test_an_agent_is_found_by_email_or_exact_name() -> None:
    contacts = [ANDY, KELSEY_A]

    assert seed.find_agent(contacts, "andy@cornerhouserealty.com") is ANDY
    assert seed.find_agent(contacts, "Andy Jang") is ANDY


def test_one_name_on_two_workbook_rows_must_be_named_by_email() -> None:
    """Kelsey Mahon is in the roster twice, under two addresses."""
    contacts = [ANDY, KELSEY_A, KELSEY_B]

    with pytest.raises(seed.SeedError, match="more than one"):
        seed.find_agent(contacts, "Kelsey Mahon")
    assert seed.find_agent(contacts, "kelsey@kelseysellshomes.com") is KELSEY_B


def test_an_unknown_agent_is_refused_with_the_near_misses_named() -> None:
    with pytest.raises(seed.SeedError, match=r"andy@cornerhouserealty\.com"):
        seed.find_agent([ANDY], "andy")


def test_a_request_type_with_no_design_is_refused_before_anything_is_written() -> None:
    """Price Reduction has no file in Generic Templates, so it cannot build."""
    designs = ["Sold", "Under Contract", "New Listing"]

    with pytest.raises(seed.SeedError, match="no design named"):
        seed.check_design(designs, "Price Reduction")


def test_a_request_type_takes_the_design_s_exact_spelling() -> None:
    """The picker matches the file name, so a case difference would stop the run."""
    assert seed.check_design(["Under Contract"], "under contract") == "Under Contract"


def test_a_row_is_laid_out_under_the_tab_s_own_header() -> None:
    listing = seed.Listing(
        request_type="Under Contract",
        address="3283 Doyle Place, Aberdeen, MD 21009",
        side="Seller",
    )

    row = seed.row_for(TESTING_HEADER, ANDY, listing)

    assert len(row) == len(TESTING_HEADER)
    named = dict(zip(TESTING_HEADER, row, strict=True))
    assert named["Email Address"] == "andy@cornerhouserealty.com"
    assert named["First Name"] == "Andy"
    assert named["Second Name"] == "Jang"
    assert named["Select your request type"] == "Under Contract"
    assert named["Property Address"] == "3283 Doyle Place, Aberdeen, MD 21009"
    assert named["For Sold or Under Contract posts, were you on the buyer or seller side?"] == (
        "Seller"
    )
    # The postcard address column is a different question and must stay empty.
    assert named["Please provide the property address for the postcard"] == ""
    assert row[0], "column A must carry a timestamp or the row is refused downstream"


def test_each_price_lands_in_the_column_its_request_type_uses() -> None:
    """A closing price is not a new price; putting one in the other pauses the run."""
    sold = seed.row_for(
        TESTING_HEADER,
        ANDY,
        seed.Listing(request_type="Sold", address="1 Main St, MD 21009", closing_price="$600,000"),
    )
    reduced = seed.row_for(
        TESTING_HEADER,
        ANDY,
        seed.Listing(
            request_type="Price Reduction", address="1 Main St, MD 21009", new_price="$550,000"
        ),
    )

    sold_named = dict(zip(TESTING_HEADER, sold, strict=True))
    reduced_named = dict(zip(TESTING_HEADER, reduced, strict=True))
    assert sold_named["Closing price (for sold posts only):"] == "$600,000"
    assert sold_named["New price (if price improvement)"] == ""
    assert reduced_named["New price (if price improvement)"] == "$550,000"
    assert reduced_named["Closing price (for sold posts only):"] == ""


def test_a_header_that_names_nothing_is_refused_rather_than_guessed() -> None:
    with pytest.raises(seed.SeedError, match="will not guess"):
        seed.row_for(
            ["Column A", "Column B", "Column C"],
            ANDY,
            seed.Listing(request_type="Sold", address="1 Main St, MD 21009"),
        )
