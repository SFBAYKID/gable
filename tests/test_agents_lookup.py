"""Looking an unknown agent up on the brokerage site, and refusing most of it."""

from __future__ import annotations

from gable.agents.lookup import OFFICE_NUMBERS, extract

PAGE = """
# Jane Doe

Jane Doe is a Realtor with Corner House Realty.

Call Jane at 410.555.0134 or email jane@cornerhouserealty.com
"""


def test_a_named_page_on_the_brokerage_site_yields_the_details() -> None:
    found = extract(PAGE, "https://cornerhouserealty.com/jane-doe/", "Jane Doe")
    assert found.phone == "410.555.0134"
    assert found.email == "jane@cornerhouserealty.com"
    assert found.source_url == "https://cornerhouserealty.com/jane-doe/"
    assert found.is_usable


def test_a_page_that_never_names_the_agent_is_refused() -> None:
    """A directory lists everyone. Its numbers belong to whoever it is about."""
    found = extract(PAGE, "https://cornerhouserealty.com/agents/", "Herb Bryant")
    assert not found.is_usable
    assert found.phone == ""


def test_details_from_anywhere_but_the_brokerage_are_refused() -> None:
    """Another Jane Doe, on another site, is a different person."""
    found = extract(PAGE, "https://zillow.com/jane-doe", "Jane Doe")
    assert not found.is_usable


def test_the_office_line_is_never_taken_as_an_agent_number() -> None:
    """It is on every page, so it would look like a successful lookup.

    Every unknown agent would then get the same number, which is exactly the
    failure the roster bug already produced once.
    """
    office = next(iter(OFFICE_NUMBERS))
    written = f"{office[:3]}.{office[3:6]}.{office[6:]}"
    page = f"# Jane Doe\n\nCorner House Realty, {written}\n"
    found = extract(page, "https://cornerhouserealty.com/jane-doe/", "Jane Doe")
    assert found.phone == ""


def test_a_page_with_a_name_but_no_contact_details_is_not_usable() -> None:
    page = "# Jane Doe\n\nJane Doe joined Corner House Realty in 2019.\n"
    assert not extract(page, "https://cornerhouserealty.com/jane-doe/", "Jane Doe").is_usable


def test_the_number_is_kept_the_way_the_site_writes_it() -> None:
    """The roster is read by people; its own punctuation is worth preserving."""
    page = "# Jane Doe\n\nJane Doe — (410) 555-0134\n"
    found = extract(page, "https://cornerhouserealty.com/jane-doe/", "Jane Doe")
    assert found.phone == "(410) 555-0134"


def test_matching_the_name_ignores_casing_and_spacing() -> None:
    page = "# JANE  DOE\n\nCall 410.555.0134\n"
    assert extract(page, "https://cornerhouserealty.com/jane-doe/", "Jane Doe").is_usable
