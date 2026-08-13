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
