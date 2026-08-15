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


def test_details_with_no_time_empty_the_time_box_rather_than_repeating_the_date() -> None:
    """Both boxes used to take the whole string, and it read as a duplicate.

    The old reasoning was that a blank box would leave the design's own "2-4PM"
    showing. It does not: an explicit empty replacement clears the box, so the
    previous listing's time is gone either way.
    """
    resolved = fields.resolve(OPEN_HOUSE_TEXT)
    pairs = fields.replacements(resolved, {"open_house": "This Sunday afternoon"})

    assert pairs["Sunday, Aug 2, 2026"] == "This Sunday afternoon"
    assert pairs["2-4PM"] == ""


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


def test_two_days_at_the_same_hours_write_that_time_once() -> None:
    """Row 98 wrote "08/08/2026 11am-1pm , 08/09/2026 11am-1pm"."""
    resolution = fields.resolve(["SUNDAY, MAY 24TH\n1 PM - 3 PM"])

    pairs = fields.replacements(
        resolution,
        {"open_house": "08/08/2026 11am-1pm , 08/09/2026 11am-1pm"},
    )

    assert pairs["SUNDAY, MAY 24TH\n1 PM - 3 PM"] == "08/08/2026, 08/09/2026\n11am-1pm"


def test_two_days_at_different_hours_keep_both_times() -> None:
    """Dropping one would be a lie about when the house is open."""
    resolution = fields.resolve(["SUNDAY, MAY 24TH\n1 PM - 3 PM"])

    pairs = fields.replacements(
        resolution,
        {"open_house": "08/08/2026 11am-1pm , 08/09/2026 2-4pm"},
    )

    assert "2-4pm" in pairs["SUNDAY, MAY 24TH\n1 PM - 3 PM"]
    assert "11am-1pm" in pairs["SUNDAY, MAY 24TH\n1 PM - 3 PM"]


def test_a_date_with_no_time_leaves_the_time_box_empty() -> None:
    """Sydney Kinney's flyer printed "7/11/2026" in both boxes."""
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "7/11/2026"})

    assert pairs["Sunday, Aug 2, 2026"] == "7/11/2026"
    # Emptied rather than repeated, and never left showing the design's own
    # "2-4PM" — that is a previous listing's real time.
    assert pairs["2-4PM"] == ""


def test_an_hour_range_without_am_or_pm_still_reaches_the_time_box() -> None:
    """Morgan Muse's flyer read "Saturday August 8, 11-1 | | $325,000".

    The whole string went into the date box because the strict time pattern
    needs a meridiem, so the time box was emptied and the design's own two
    separators stood either side of a gap.
    """
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "Saturday August 8, 11-1"})

    assert pairs["Sunday, Aug 2, 2026"] == "Saturday August 8"
    assert pairs["2-4PM"] == "11-1"


def test_a_range_of_days_is_not_mistaken_for_a_range_of_hours() -> None:
    """A range of days: splitting it would leave "Aug" as the whole date."""
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "Aug 8-9"})

    assert pairs["Sunday, Aug 2, 2026"] == "Aug 8-9"
    assert pairs["2-4PM"] == ""


def test_two_days_and_a_bare_time_split_at_the_time() -> None:
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "August 8-9, 11-1"})

    assert pairs["Sunday, Aug 2, 2026"] == "August 8-9"
    assert pairs["2-4PM"] == "11-1"


def test_a_bare_range_in_the_middle_is_left_alone() -> None:
    """Only a trailing range is unambiguous enough to move."""
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "11-1 on Saturday"})

    assert pairs["Sunday, Aug 2, 2026"] == "11-1 on Saturday"


def test_two_days_at_the_same_bare_hours_write_that_time_once() -> None:
    """The meridiem forms were cured of this; the bare forms re-manifested it.

    "Aug 8, 11-1 and Aug 9, 11-1" measured its times with the meridiem pattern
    alone, found none, and left the first "11-1" standing in the date box above
    its own time box.
    """
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "Aug 8, 11-1 and Aug 9, 11-1"})

    assert pairs["2-4PM"] == "11-1"
    assert "11-1" not in pairs["Sunday, Aug 2, 2026"]
    assert "Aug 8" in pairs["Sunday, Aug 2, 2026"]
    assert "Aug 9" in pairs["Sunday, Aug 2, 2026"]


def test_two_days_at_different_bare_hours_do_not_split() -> None:
    """A promoted bare "2-4" would hide Saturday's own hours in the date line."""
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "Aug 8, 11-1 and Aug 9, 2-4"})

    assert pairs["Sunday, Aug 2, 2026"] == "Aug 8, 11-1 and Aug 9, 2-4"
    assert pairs["2-4PM"] == ""


def test_a_time_with_no_date_is_not_printed_twice() -> None:
    """A time alone used to fall back into the date box beside its own time box."""
    resolution = fields.resolve(["Sunday, Aug 2, 2026", "2-4PM"])

    pairs = fields.replacements(resolution, {"open_house": "2-4PM"})

    assert pairs["2-4PM"] == "2-4PM"
    # Cleared, not repeated: an explicit empty replacement clears the sample.
    assert pairs["Sunday, Aug 2, 2026"] == ""


def test_a_time_with_no_date_fills_the_one_box_design_once() -> None:
    resolution = fields.resolve(["SUNDAY, MAY 24TH\n1 PM - 3 PM"])

    pairs = fields.replacements(resolution, {"open_house": "2-4PM"})

    assert pairs["SUNDAY, MAY 24TH\n1 PM - 3 PM"] == "2-4PM"
