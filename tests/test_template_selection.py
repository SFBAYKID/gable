"""Tests for notes-aware template purpose and selection."""

from __future__ import annotations

from gable.listings.intake import Intake
from gable.slides.catalog import CATALOG
from gable.slides.selection import purpose_for, rank, signals_for, template_picker


def _intake(**overrides: str) -> Intake:
    values = {
        "agent_email": "chase@monarchconnected.com",
        "agent_name": "Chase Gonzales",
        "request_type": "New Listing",
        "address": "123 Main St, Baltimore, MD 21201",
        "post_details": "",
        "open_house": "",
        "new_price": "",
        "closing_price": "",
        "extra_notes": "",
        "side": "",
        "notes": "",
    }
    values.update(overrides)
    return Intake(**values)


def test_every_catalogue_entry_has_human_readable_usage_metadata() -> None:
    assert len(CATALOG) == 45
    for entry in CATALOG:
        purpose = purpose_for(entry)
        assert purpose.use_when
        assert entry.category.lower() in purpose.use_when
        assert purpose.agent_count in {1, 2}


def test_plain_new_listing_uses_the_documented_clean_default() -> None:
    chosen = rank("Just Listed", _intake())
    assert chosen and chosen[0].slide == 15


def test_private_tour_language_in_extra_notes_selects_the_tour_layout() -> None:
    chosen = rank(
        "Just Listed",
        _intake(extra_notes="Please emphasize scheduling a private tour."),
    )
    assert chosen and chosen[0].slide == 11


def test_two_agents_in_notes_select_the_dual_hosted_open_house_layout() -> None:
    intake = _intake(
        request_type="New Listing with Open House",
        open_house="Saturday at 1 PM",
        notes="Listed by: Stacey Abbott. Hosted by: Jason Vetter.",
    )
    chosen = rank("Just Listed", intake)
    assert signals_for(intake).agent_count == 2
    assert chosen and chosen[0].slide == 16


def test_single_agent_two_date_open_house_uses_the_sat_and_sun_layout() -> None:
    intake = _intake(
        request_type="Open House",
        open_house="Saturday 12 to 2 and Sunday 1 to 3",
    )
    chosen = rank("Open House", intake)
    assert signals_for(intake).has_two_dates is True
    assert chosen and chosen[0].slide == 31


def test_two_agent_two_date_open_house_uses_the_matching_dual_layout() -> None:
    intake = _intake(
        request_type="Open House",
        open_house="Saturday 12 to 2 and Sunday 1 to 3",
        post_details="Listed by: Stacey Abbott. Hosted by: Jason Vetter.",
    )
    chosen = rank("Open House", intake)
    assert chosen and chosen[0].slide == 28


def test_a_dual_agent_request_never_falls_back_to_a_single_agent_design() -> None:
    intake = _intake(
        post_details="Listed by: Stacey Abbott. Hosted by: Jason Vetter.",
    )
    assert rank("Just Listed", intake) == ()


def test_the_template_named_for_the_request_type_is_the_one_used() -> None:
    """The contract Chase set with Carmen: the form's word is the file's name."""
    picker = template_picker(lambda: [{"id": "sold-file", "name": "Sold"}])
    assert picker("Just Sold", _intake(request_type="Sold")) == ("sold-file", "Sold")


def test_matching_survives_casing_and_stray_spacing() -> None:
    """Carmen names files by hand; "sold " and "Sold" are the same design."""
    picker = template_picker(lambda: [{"id": "sold-file", "name": " sold "}])
    assert picker("", _intake(request_type="Sold"))[0] == "sold-file"


def test_a_request_type_with_no_template_named_for_it_asks() -> None:
    """Nothing close is picked. There is one rule and it either matches or not."""
    picker = template_picker(lambda: [{"id": "sold-file", "name": "Sold"}])
    assert picker("", _intake(request_type="Open House")) == ("", "")


def test_a_catalogue_style_name_no_longer_matches() -> None:
    """The scored catalogue is gone; "Just Sold — Thinking of Selling" is not "Sold"."""
    picker = template_picker(lambda: [{"id": "old", "name": "Just Sold — Thinking of Selling"}])
    assert picker("Just Sold", _intake(request_type="Sold")) == ("", "")


def test_two_templates_with_the_same_name_are_refused() -> None:
    """A filing mistake must not render a real flyer off a coin flip."""
    picker = template_picker(lambda: [{"id": "one", "name": "Sold"}, {"id": "two", "name": "Sold"}])
    assert picker("", _intake(request_type="Sold")) == ("", "")


def test_a_submission_with_no_request_type_asks() -> None:
    picker = template_picker(lambda: [{"id": "sold-file", "name": "Sold"}])
    assert picker("", _intake(request_type="")) == ("", "")
