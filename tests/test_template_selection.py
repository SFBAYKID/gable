"""The template naming rule: the file is named what the form asked for."""

from __future__ import annotations

from gable.listings.intake import Intake
from gable.slides.selection import template_picker


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
