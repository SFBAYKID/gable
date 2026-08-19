"""Regression tests for resolving the template's literal sample content."""

from gable.slides import fields


def test_a_combined_cell_and_office_phone_box_is_recognised_after_normalising() -> None:
    literal = "C: 410.456.6868\nO: 443.499.3839"

    resolution = fields.resolve([literal])

    assert resolution.fields["agent_phone"] == literal


def test_a_known_long_sample_testimonial_is_resolved_before_the_short_field_filter() -> None:
    literal = (
        '"Working with Corner House Realty was such a smooth experience from '
        "the first call through closing, and I would recommend the team to anyone."
    )

    resolution = fields.resolve([literal])

    assert resolution.fields["review_quote"] == literal
    assert fields.replacements(resolution, {"review_quote": "The real client review."}) == {
        literal: "The real client review."
    }


def test_a_bare_realtor_credential_is_data_not_brand_copy() -> None:
    """A source must not grant a membership credential to an arbitrary agent."""
    resolution = fields.resolve(["Realtor"])

    assert resolution.fields == {"agent_title": "Realtor"}
    assert fields.replacements(resolution, {"agent_title": ""}) == {}


def test_an_unfilled_stat_is_blanked_rather_than_left_as_the_designs_number() -> None:
    """Three delivered flyers stated a number nobody supplied.

    Mike Nugent's flyer read "3 Bathrooms" because New Listing is drawn with
    three, and Carmen had given only beds, square footage and price. A reader
    cannot tell that from a supplied figure, so it is a claim about somebody's
    house that nobody made.
    """
    resolution = fields.Resolution(
        fields={"beds": "4", "baths": "3", "square_feet": "2,430", "price": "$350,000"},
        also={},
    )

    pairs = fields.replacements(
        resolution, {"beds": "3", "square_feet": "1,880", "price": "$379,000"}
    )

    assert pairs["3"] == "", "the bathrooms slot is emptied, not left showing"
    assert pairs["4"] == "3"
    assert pairs["2,430"] == "1,880"
    assert pairs["$350,000"] == "$379,000"


def test_a_placeholder_that_reads_as_a_gap_is_still_left_showing() -> None:
    """The rule is narrow: only slots whose placeholder looks like real data."""
    resolution = fields.Resolution(
        fields={"address": "PROPERTY ADDRESS", "listing_note": "Ready to Buy?"},
        also={},
    )

    pairs = fields.replacements(resolution, {})

    assert pairs == {}, "an obvious placeholder still survives to be asked about"


def test_two_fields_sharing_one_placeholder_are_reported() -> None:
    """A design drawn 3 bed / 3 bath would fill both slots with one number.

    `replacements` is keyed by the literal, so the second field overwrites the
    first and `replace_text` swaps every occurrence. The standalone-literal
    guard cannot see it: both occurrences really are standalone. None of the
    six designs does this today; a redrawn one could.
    """
    resolution = fields.Resolution(fields={"beds": "3", "baths": "3"}, also={})

    assert fields.fields_sharing_a_literal(resolution) == {"3": ["beds", "baths"]}


def test_an_unambiguous_design_reports_nothing() -> None:
    resolution = fields.Resolution(fields={"beds": "4", "baths": "3"}, also={})

    assert fields.fields_sharing_a_literal(resolution) == {}
