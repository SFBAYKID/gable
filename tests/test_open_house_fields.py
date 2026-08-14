"""Reading the Open House design, which carries live sample data.

Its beds, baths, date and time are a previous listing's real details rather
than bracketed placeholders. Leaving them unrecognised was not neutral: the
run stopped at needs_template, and any box Gable did not fill would have put
somebody else's open house on the flyer.
"""

from __future__ import annotations

from gable.slides import fields

#: Every text run on the live Open House design, read 2026-08-14.
OPEN_HOUSE_TEXT: list[str] = [
    "Open House",
    "5066 Winesap Way, Ellicott City, MD 21043",
    "Sunday, Aug 2, 2026",
    "2-4PM",
    "$1,199,000",
    "5 BEDS",
    "5 BATHS",
    "6,348 SQFT",
    "Louis Smith",
    "Realtor",
    "Tour this home with Corner House Realty",
    "410-564-6618",
    "louis@cornerhouserealty.com",
]


def test_the_live_sample_beds_and_baths_are_recognised() -> None:
    """"5 BEDS" with no bracket stopped the run at needs_template."""
    resolved = fields.resolve(OPEN_HOUSE_TEXT)

    assert resolved.fields["beds"] == "5 BEDS"
    assert resolved.fields["baths"] == "5 BATHS"


def test_the_sample_date_and_time_are_recognised() -> None:
    """An unfilled box keeps the design's own real date and time."""
    resolved = fields.resolve(OPEN_HOUSE_TEXT)

    literals = {resolved.fields["open_house"], *resolved.also.get("open_house", ())}
    assert "Sunday, Aug 2, 2026" in literals
    assert "2-4PM" in literals


def test_the_date_box_takes_the_date_and_the_time_box_the_time() -> None:
    """Filling both with the whole string reads as a duplicate."""
    resolved = fields.resolve(OPEN_HOUSE_TEXT)
    pairs = fields.replacements(resolved, {"open_house": "Saturday, Sep 6, 2026 1-3PM"})

    assert pairs["Sunday, Aug 2, 2026"] == "Saturday, Sep 6, 2026"
    assert pairs["2-4PM"] == "1-3PM"


def test_details_with_no_time_fill_both_boxes_rather_than_leaving_a_sample() -> None:
    """A blank box would show a previous listing's real open house."""
    resolved = fields.resolve(OPEN_HOUSE_TEXT)
    pairs = fields.replacements(resolved, {"open_house": "This Sunday afternoon"})

    assert pairs["Sunday, Aug 2, 2026"] == "This Sunday afternoon"
    assert pairs["2-4PM"] == "This Sunday afternoon"


def test_brand_copy_is_not_mistaken_for_a_date_or_a_time() -> None:
    resolved = fields.resolve(OPEN_HOUSE_TEXT)
    filled = set(resolved.fields.values()) | {
        literal for extras in resolved.also.values() for literal in extras
    }

    assert "Open House" not in filled
    assert "Tour this home with Corner House Realty" not in filled
    assert "Local expertise. Exceptional results." not in filled
