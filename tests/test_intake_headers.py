"""Finding the columns by name, on both tab shapes that really exist.

The headers below are transcriptions of the live sheet, read through the service
account on 2026-08-12. They are the point of these tests: a fixture that invents
header text proves the matcher works on a tab nobody has.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from gable.db.schema import apply_migrations, connect
from gable.listings.intake import (
    DEFAULT_COLUMNS,
    columns_from_header,
    from_row,
    maps_a_response_row,
)
from gable.sheets import repository as repo
from gable.sheets.client import SheetError

#: `Form Responses 1`, header on row 1. One column per question.
LIVE_HEADER: list[str] = [
    "Column 1",
    "Email Address",
    "Name of Agent",
    "Service Guidelines Acknowledgment",
    "Select your request type",
    "Please provide the property address for the postcard",
    "Select postcard category",
    "Upload photos",
    "Upload your video assets (For Video Editing Requests Only)",
    "Optional: Include any details/instruction for your video",
    "Select social media content type",
    "Property Address",
    (
        "Upload high-resolution property photos (up to 5 images)\n"
        "Please note: Bright screen grabs are acceptable for Buyer's Agent "
        "requests, otherwise, please use high-res images from HomeTrack"
    ),
    "Include details for post - required for Client Review post",
    "Open house date/time (if applicable)",
    "New price (if price improvement)",
    "Closing price (for sold posts only):",
    "Additional Notes for Social Media Team",
    "For Sold or Under Contract posts, were you on the buyer or seller side?",
    "Notes",
]

#: `Testing_1`, header on row 2 under a blank row, with the name split in two.
#: Everything from D rightward therefore sits one column further right.
TESTING_HEADER: list[str] = [
    "r",
    "Email Address",
    "First Name",
    "Second Name",
    *LIVE_HEADER[3:],
]

#: `Testing_1` row 78, as the sheet returns it.
ROW_78: list[str] = [
    "7/29/2026 10:00:12",
    "eric@cornerhouserealty.com",
    "Eric",
    "jacobs",
    "I understand that requests are fulfilled Monday through Friday...",
    "Sold",
    "",
    "",
    "",
    "",
    "",
    "Instagram Story",
    "23 pierside ave unit 118 Baltimore Md 21230",
    "",
    "Na",
    "",
    "",
    "330000",
    "",
    "Seller",
]


def test_the_live_tab_maps_exactly_where_it_always_did() -> None:
    """The production tab must not move. Reading by name is a no-op there."""
    assert columns_from_header(LIVE_HEADER) == DEFAULT_COLUMNS


def test_the_split_name_tab_shifts_every_later_column_by_one() -> None:
    """`Testing_1` is the shape that made positions unusable."""
    columns = columns_from_header(TESTING_HEADER)
    assert columns["agent_first_name"] == 2
    assert columns["agent_last_name"] == 3
    assert columns["request_type"] == 5
    assert columns["address"] == 12
    assert columns["closing_price"] == 17
    assert "agent_name" not in columns


def test_row_78_reads_as_the_sold_listing_it_is() -> None:
    """The whole point: the same row, read correctly instead of plausibly."""
    intake = from_row(ROW_78, columns_from_header(TESTING_HEADER))
    assert intake.request_type == "Sold"
    assert intake.address == "23 pierside ave unit 118 Baltimore Md 21230"
    assert intake.closing_price == "330000"
    assert intake.agent_name == "Eric jacobs"
    assert intake.category == "Just Sold"


def test_reading_row_78_positionally_is_the_bug_this_replaces() -> None:
    """Kept as evidence: the old mapping produced confident nonsense."""
    wrong = from_row(ROW_78)
    assert wrong.address == "Instagram Story"
    assert wrong.category == ""
    assert wrong.price == ""


def test_the_postcard_address_never_wins_the_property_address() -> None:
    """Two address questions; only one belongs on a social post."""
    columns = columns_from_header(LIVE_HEADER)
    assert columns["address"] == LIVE_HEADER.index("Property Address")


def test_the_team_notes_are_not_the_notes_column() -> None:
    """`Notes` is exact so the social team's note cannot land in it."""
    columns = columns_from_header(LIVE_HEADER)
    assert columns["notes"] == LIVE_HEADER.index("Notes")
    assert columns["extra_notes"] == LIVE_HEADER.index("Additional Notes for Social Media Team")


def test_a_missing_name_column_still_maps_when_the_pair_is_present() -> None:
    assert maps_a_response_row(columns_from_header(TESTING_HEADER))
    assert maps_a_response_row(columns_from_header(LIVE_HEADER))


@pytest.mark.parametrize(
    "header",
    [
        [],
        ["Timestamp", "Email Address", "Name of Agent"],  # no request type, no address
        ["Email Address", "Select your request type"],  # no address
    ],
)
def test_an_unrecognisable_header_is_refused(header: list[str]) -> None:
    """Guessing here reads real values into the wrong fields."""
    assert not maps_a_response_row(columns_from_header(header))


def test_find_header_skips_a_blank_leading_row() -> None:
    index, columns = repo.find_header([[], TESTING_HEADER, ROW_78])
    assert index == 1
    assert columns["address"] == 12


def test_find_header_refuses_a_tab_it_does_not_recognise() -> None:
    with pytest.raises(SheetError):
        repo.find_header([["a", "b"], ["1", "2"]])


def test_a_row_read_by_hand_has_the_same_identity_as_a_polled_one() -> None:
    """Otherwise starting a row manually would build a second flyer for it."""
    columns = columns_from_header(TESTING_HEADER)
    by_hand = repo.submission_from_row(ROW_78, columns, 78)

    class _Sheet:
        def read(self, _range: str) -> list[list[str]]:
            return [[], TESTING_HEADER, *[[] for _ in range(75)], ROW_78]

    polled = repo.read_submissions(_Sheet(), "Testing_1")
    assert [s.response_row_id for s in polled] == [by_hand.response_row_id]
    assert polled[0].sheet_row == by_hand.sheet_row == 78


# --- the roster ------------------------------------------------------------

ROSTER_HEADER: list[str] = [
    "Email",
    "First Name",
    "Last Name",
    "Phone",
    "Headshot URL",
    "Brokerage URL",
]
ROSTER_ROW: list[str] = [
    "eric@cornerhouserealty.com",
    "Eric",
    "Jacobs",
    "443.682.1767",
    "https://example.invalid/eric.jpg",
    "https://cornerhouserealty.com/eric-jacobs/",
]


def test_the_roster_header_is_found_wherever_it_sits() -> None:
    """It has already moved from row 2 to row 1 once."""
    assert repo.find_roster_header([ROSTER_HEADER, ROSTER_ROW])[0] == 0
    assert repo.find_roster_header([[], ROSTER_HEADER, ROSTER_ROW])[0] == 1


def test_a_roster_read_from_the_wrong_row_is_refused_not_silently_empty() -> None:
    """Storing nobody looked identical to having nobody, for a whole day.

    Reading the roster from row 2 made Andy Jang's details the column names, so
    every lookup missed and the flyer printed the office number and the design's
    own stock face on a real agent's post.
    """
    with pytest.raises(SheetError):
        repo.find_roster_header([ROSTER_ROW])


def test_every_person_after_the_header_is_stored() -> None:
    connection = connect(Path(tempfile.mkdtemp()) / "roster.db")
    apply_migrations(connection)

    class _Sheet:
        def read(self, _range: str) -> list[list[str]]:
            return [ROSTER_HEADER, ROSTER_ROW]

    assert repo.sync_salespeople(_Sheet(), connection, "Sales_People") == 1
    found = repo.find_salesperson(connection, "eric@cornerhouserealty.com")
    assert found["first_name"] == "Eric"
    assert found["last_name"] == "Jacobs"
    assert found["phone"] == "443.682.1767"
    assert found["headshot_url"] == "https://example.invalid/eric.jpg"
