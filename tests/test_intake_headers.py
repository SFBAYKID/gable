"""Finding the columns by name, on both tab shapes that really exist.

The headers below are transcriptions of the live sheet, read through the service
account on 2026-08-12. They are the point of these tests: a fixture that invents
header text proves the matcher works on a tab nobody has.

`LIVE_HEADER` is that 2026-08-12 shape and is **no longer what the live tab
looks like**. Re-read on 2026-08-17, `Form Responses 1` splits the name into
`First Name of Agent` / `Last Name of Agent` and has dropped its trailing
`Notes`, so it now sits one column right of this — much closer to `Testing_1`.
The current shape is exercised in `tests/test_content_type_gate.py`. This
fixture is kept because it is the shape `DEFAULT_COLUMNS` was written from, and
the pair proves the matcher survives the form being edited underneath it.
"""

from __future__ import annotations

import pytest

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
    by_hand = repo.submission_from_row(ROW_78, columns, 78, source_tab="Testing_1")

    class _Sheet:
        def read(self, _range: str) -> list[list[str]]:
            return [[], TESTING_HEADER, *[[] for _ in range(75)], ROW_78]

    polled = repo.read_submissions(_Sheet(), "Testing_1")
    assert [s.response_row_id for s in polled] == [by_hand.response_row_id]
    assert polled[0].sheet_row == by_hand.sheet_row == 78
    assert polled[0].source_tab == "Testing_1"


def test_correcting_identity_fields_changes_the_legacy_tuple_hash() -> None:
    """The repository reconciles this hash change against stored timestamp state."""
    columns = columns_from_header(TESTING_HEADER)
    original = repo.submission_from_row(ROW_78, columns, 78)
    corrected_row = ROW_78.copy()
    corrected_row[1] = "corrected@example.com"
    corrected_row[12] = "900 Corrected Address"

    corrected = repo.submission_from_row(corrected_row, columns, 78)

    assert corrected.response_row_id != original.response_row_id
    assert corrected.content_hash != original.content_hash


def test_two_rows_with_the_same_timestamp_are_read_as_distinct_rows() -> None:
    duplicate = ROW_78.copy()
    duplicate[12] = "900 Different Address"

    class _Sheet:
        def read(self, _range: str) -> list[list[str]]:
            return [TESTING_HEADER, ROW_78, duplicate]

    submissions = repo.read_submissions(_Sheet(), "Testing_1")

    assert [item.intake.address for item in submissions] == [
        ROW_78[12],
        "900 Different Address",
    ]
    assert submissions[0].response_row_id != submissions[1].response_row_id


def test_a_row_without_a_timestamp_is_refused() -> None:
    row = ROW_78.copy()
    row[0] = ""

    with pytest.raises(SheetError, match="no submission timestamp"):
        repo.submission_from_row(row, columns_from_header(TESTING_HEADER), 78)


#: The live `Form Responses 1` header row, read from the production tab on
#: 2026-08-13. Kept verbatim: it is the shape polling must survive.
PRODUCTION_HEADER: list[str] = [
    "Timestamp",
    "Email Address",
    "First Name of Agent",
    "Service Guidelines Acknowledgment",
    "Select your request type",
    "Please provide the property address for the postcard",
    "Select postcard category",
    "Upload photos",
    "Upload your video assets (For Video Editing Requests only)",
    "Optional: Include any details/instruction for your video",
    "Select social media content type",
    "Property Address",
    "Upload high-resolution property photos (up to 5 images)",
    "Include details for post - required for Client Review post",
    "Open House Date/Time (if applicable)",
    "New Price (if price improvement)",
    "Closing Price (for sold posts only):",
    "Additional notes for social media team",
    "For sold or under contract posts, were you on the buyer or seller side?",
    "Last Name of Agent",
    "Notes",
]


def test_the_live_production_header_is_a_readable_response_tab() -> None:
    """Polling must read the real form, not only the testing tab.

    `Testing_1` asks "First Name" / "Second Name"; production asks "First Name
    of Agent" / "Last Name of Agent". Exact matching found no name column on
    production, so the whole tab was refused and polling could not have read a
    single genuine submission — while every test on `Testing_1` passed.
    """
    columns = columns_from_header(PRODUCTION_HEADER)

    assert maps_a_response_row(columns)
    assert columns["agent_first_name"] == 2
    assert columns["agent_last_name"] == 19
    assert columns["agent_email"] == 1
    assert columns["request_type"] == 4
    assert columns["address"] == 11


def test_the_older_testing_tab_wording_still_reads() -> None:
    """The prefix rule must not break the tab every live test has used."""
    header = [
        "r",
        "Email Address",
        "First Name",
        "Second Name",
        "Service Guidelines Acknowledgment",
        "Select your request type",
        "Please provide the property address for the postcard",
        "Select postcard category",
        "Upload photos",
        "Upload your video assets (For Video Editing Requests only)",
        "Optional: Include any details/instruction for your video",
        "Select social media content type",
        "Property Address",
    ]

    columns = columns_from_header(header)

    assert maps_a_response_row(columns)
    assert columns["agent_first_name"] == 2
    assert columns["agent_last_name"] == 3


def test_a_name_prefix_cannot_swallow_the_address_or_request_columns() -> None:
    """Loosening to prefix must not let a name rule claim another field."""
    columns = columns_from_header(PRODUCTION_HEADER)

    assert columns["agent_first_name"] != columns["address"]
    assert columns["agent_last_name"] != columns["address"]
    assert columns["agent_first_name"] != columns["request_type"]
    # "Please provide the property address..." must never win `address`; the
    # exact "Property Address" column is the one that carries a usable value.
    assert columns["address"] == 11
