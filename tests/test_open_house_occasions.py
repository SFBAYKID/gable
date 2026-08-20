"""One design, one date, one time -- and what happens when a request names three.

Split out of `test_slides_preflight.py` at the 800-line ceiling. Every case here
comes from a real submission: Effie Fafaleos' 2026-08-20 Open House named three
across three days, and Gable answered it by asking for a wider template.
"""

from __future__ import annotations

from gable.slackapp.style import violations
from gable.slides import fields
from tests.test_slides_preflight import _analyze, _presentation, _text


def test_three_open_houses_ask_which_one_rather_than_for_a_wider_box() -> None:
    """Effie Fafaleos' 2026-08-20 request, which stranded a real listing.

    The design has one date box and one time box. Three open houses do not fit
    any width of those, so "Widen that section" was a remedy that could not
    work -- and a wider box would have shipped the mangled split beneath.
    """
    value = "Friday, Aug. 21 4pm to 6pm, Sat. Aug. 22 10am to 12pm, Sun, Aug. 23 11am to 1pm"

    assert fields.open_house_occasions(value) == 3


def test_one_open_house_held_on_two_days_is_still_one_open_house() -> None:
    """The same hours written twice must not become a question."""
    assert fields.open_house_occasions("08/08/2026 11am-1pm , 08/09/2026 11am-1pm") == 1
    assert fields.open_house_occasions("Aug 8, 11-1 and Aug 9, 11-1") == 1
    assert fields.open_house_occasions("Saturday and Sunday, August 22-23, 12:00PM-2:00PM") == 1


def test_a_date_with_no_time_names_no_open_house_hours() -> None:
    """A value carrying no time at all is not a multi-occasion request."""
    assert fields.open_house_occasions("7/11/2026") == 0
    assert fields.open_house_occasions("") == 0


def test_only_the_first_occasion_reaches_the_two_boxes() -> None:
    """The old split left the other two open houses standing in the date box.

    It read "Friday, Aug. 21, Sat. Aug. 22 10am to 12pm, Sun, Aug. 23 11am to
    1pm" above a time box saying "4pm to 6pm" -- one time asserting itself as
    THE time while the others hid in the line above. Only the width check
    stopped it reaching a flyer.
    """
    value = "Friday, Aug. 21 4pm to 6pm, Sat. Aug. 22 10am to 12pm, Sun, Aug. 23 11am to 1pm"

    date_part = fields._open_house_part("SATURDAY, JUNE 7", value)
    time_part = fields._open_house_part("12PM - 2PM", value)

    assert date_part == "Friday, Aug. 21"
    assert time_part == "4-6PM", "the design draws a 72pt time box; longhand overflowed it"
    assert "Sat." not in date_part and "Sun" not in date_part
    assert fields.first_open_house(value) == "Friday, Aug. 21 4pm to 6pm"
    assert fields.dropped_open_houses(value) == (
        "Sat. Aug. 22 10am to 12pm, Sun, Aug. 23 11am to 1pm"
    )


def test_several_open_houses_build_the_first_and_name_what_was_left_off() -> None:
    """End to end: a flyer exists, and the thread says exactly what it omits."""
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _text("open-date", "SATURDAY, JUNE 7", 200),
        _text("open-time", "12PM - 2PM", 120),
    )

    report = _analyze(
        presentation,
        {
            "address": "7631 Old Columbia Rd, Laurel, MD 20723",
            "open_house": (
                "Friday, Aug. 21 4pm to 6pm, Sat. Aug. 22 10am to 12pm, Sun, Aug. 23 11am to 1pm"
            ),
        },
    )

    # Nothing is blocked: the flyer gets built.
    assert report.blockers == ()
    issue = next(item for item in report.warnings if item.code == "several_open_houses")
    assert "3 open houses" in issue.advisory
    assert "Friday, Aug. 21 4pm to 6pm" in issue.advisory, "it names what it used"
    assert "Sat. Aug. 22 10am to 12pm" in issue.advisory, "and what it did not"
    assert "rebuild" in issue.advisory, "and how to change it"
    # The width complaint about the same field does not also go out.
    assert not any(item.code == "unreadable_open_house" for item in report.issues)
    assert not violations(issue.advisory)


def test_one_open_house_that_fits_raises_no_question_at_all() -> None:
    """The check must not fire on the ordinary single open house."""
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _text("open-date", "SATURDAY, JUNE 7", 260),
        _text("open-time", "12PM - 2PM", 160),
    )

    report = _analyze(
        presentation,
        {
            "address": "7631 Old Columbia Rd, Laurel, MD 20723",
            "open_house": "Saturday, Aug. 22 12pm to 2pm",
        },
    )

    assert not any(item.code == "several_open_houses" for item in report.issues)
    assert report.blockers == ()


def test_a_longhand_time_is_written_the_way_the_design_writes_it() -> None:
    """Effie Fafaleos' flyer printed "6pm" across "3 BATHS".

    The Open House time box is 72pt wide and holds "2-4PM" at 24pt. "4pm to
    6pm" wrapped to three lines and overflowed downward into the stats row.
    Gable said the fit was too small to read and delivered it anyway -- right
    for a flyer, wrong for a value it could have written compactly.
    """
    assert fields.compact_time("4pm to 6pm") == "4-6PM"
    assert fields.compact_time("4 p.m. to 6 p.m.") == "4-6PM"
    assert fields.compact_time("1 PM - 3 PM") == "1-3PM"
    assert fields.compact_time("12:00 PM - 2:00 PM") == "12:00-2:00PM"


def test_two_different_meridiems_both_survive() -> None:
    """Both halves survive when the meridiems differ, or it says something else."""
    assert fields.compact_time("10am to 12pm") == "10AM-12PM"
    assert fields.compact_time("11am-1pm") == "11AM-1PM"


def test_a_time_already_compact_or_unparsable_is_left_alone() -> None:
    """Nothing is rewritten that was not a plain range to begin with."""
    assert fields.compact_time("2-4PM") == "2-4PM"
    assert fields.compact_time("11-1") == "11-1"
    assert fields.compact_time("by appointment") == "by appointment"
    assert fields.compact_time("") == ""
