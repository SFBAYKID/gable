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
    """An unbracketed 5 BEDS literal used to stop the run at needs_template."""
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


#: New Listing with Open House puts the date and the time in ONE box, on two
#: lines, inside the tag shape. Read from the live design 2026-08-14.
NLWOH_TEXT: list[str] = [
    "JUST LISTED",
    "REALTOR",
    "C: 410.456.6868\nO: 443.499.3839",
    "Kelsey Mahon",
    "3822 6th St, Baltimore, MD 21225",
    "$450,000",
    "Local experts. Modern approach.  Exceptional results.",
    "Open House",
    "SUNDAY, MAY 24TH\n1 PM - 3 PM",
    "QUESTIONS ABOUT THIS PROPERTY?",
    "DM me!",
]


def test_a_date_and_time_sharing_one_box_are_recognised() -> None:
    """Neither single-line pattern matched, so the tag kept a real past date."""
    resolved = fields.resolve(NLWOH_TEXT)

    assert resolved.fields["open_house"] == "SUNDAY, MAY 24TH\n1 PM - 3 PM"


def test_that_box_is_filled_on_two_lines_as_the_design_draws_it() -> None:
    resolved = fields.resolve(NLWOH_TEXT)
    pairs = fields.replacements(resolved, {"open_house": "Sunday, Sep 6, 2026 1-3PM"})

    assert pairs["SUNDAY, MAY 24TH\n1 PM - 3 PM"] == "Sunday, Sep 6, 2026\n1-3PM"


def test_the_sample_agent_and_listing_on_that_design_are_still_recognised() -> None:
    """Kelsey Mahon's real details must never survive onto another flyer."""
    resolved = fields.resolve(NLWOH_TEXT)

    assert resolved.fields["agent_name"] == "Kelsey Mahon"
    assert resolved.fields["address"] == "3822 6th St, Baltimore, MD 21225"
    assert resolved.fields["agent_phone"] == "C: 410.456.6868\nO: 443.499.3839"


def test_a_supplied_count_keeps_the_design_s_own_unit_and_capitals() -> None:
    r"""An answered listing rendered "4 beds" where the design reads "5 BEDS".

    The words a person types are not the design's. Open House writes its counts
    as "5 BEDS" and "6,348 SQFT" and New Listing writes them on two lines as
    "4\\nBedrooms"; filling those with the reply verbatim replaced the design's
    own label, so the unit disappeared and the capitals with it.
    """
    assert fields._as_written("beds", "5 BEDS", "4 beds") == "4 BEDS"
    assert fields._as_written("baths", "5 BATHS", "3 baths") == "3 BATHS"
    assert fields._as_written("square_feet", "6,348 SQFT", "2,450") == "2,450 SQFT"
    assert fields._as_written("beds", "4\nBedrooms", "4 beds") == "4\nBedrooms"
    assert fields._as_written("square_feet", "2,430\nSq FT", "2,450") == "2,450\nSq FT"


def test_a_design_that_writes_no_unit_gets_the_number_alone() -> None:
    """A bracketed placeholder labels itself elsewhere, so nothing is invented."""
    assert fields._as_written("beds", "[ BEDS ]", "4 beds") == "4"


def test_a_count_with_no_number_is_left_exactly_as_supplied() -> None:
    """A value like Studio is not a number, and rewriting it would lose it."""
    assert fields._as_written("beds", "5 BEDS", "Studio") == "Studio"


def test_a_date_keeps_no_joining_word_once_its_time_has_moved_boxes() -> None:
    """Row 81 wrote "08/01 and 08/02 from 12-2pm" across the design's two boxes."""
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "08/01 and 08/02 from 12-2pm"})

    assert pairs["Sunday, Aug 2, 2026"] == "08/01 and 08/02"
    assert pairs["2-4PM"] == "12-2pm"


def test_a_date_that_reads_naturally_is_not_trimmed() -> None:
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "Saturday, Sep 6, 2026 1-3PM"})

    assert pairs["Sunday, Aug 2, 2026"] == "Saturday, Sep 6, 2026"
