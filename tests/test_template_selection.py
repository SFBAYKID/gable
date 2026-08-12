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


def test_picker_requires_the_exact_best_template_to_exist_in_drive() -> None:
    intake = _intake(extra_notes="Please emphasize scheduling a private tour.")
    picker = template_picker(
        lambda: [
            {
                "id": "fallback",
                "name": "Just Listed — Bracket Placeholders (cleanest)",
            }
        ]
    )
    assert picker("Just Listed", intake) == ("", "")


def test_picker_returns_the_notes_selected_drive_file() -> None:
    intake = _intake(extra_notes="Please emphasize scheduling a private tour.")
    picker = template_picker(
        lambda: [
            {
                "id": "tour-template",
                "name": "Just Listed — Schedule a Private Tour",
            }
        ]
    )
    assert picker("Just Listed", intake) == (
        "tour-template",
        "Just Listed — Schedule a Private Tour",
    )
