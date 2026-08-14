"""Reading the Client Review Post design.

Every text run on the live design, read 2026-08-14. Three of its pieces were
unrecognised: the section heading was reported as an unnamed fillable field and
stopped the run, and the sample review and the sample agent in the footer would
both have survived onto a real agent's flyer.
"""

from __future__ import annotations

from gable.slides import fields

CLIENT_REVIEW_TEXT: list[str] = [
    "REAL PEOPLE\nREAL RESULTS",
    "CLIENT TESTIMONIAL",
    (
        "Review goes here...Working with Corner House Realty was such a smooth and positive "
        "experience. Their team was knowledgeable, responsive, and truly made us feel "
        "supported every step of the way. We felt confident, informed, and cared for from "
        "start to finish. Review goes here...ccccccccc"
    ),
    "Let’s Find Your Corner.",  # noqa: RUF001 - verbatim from the design
    "443-499-3839",
    "sebastian@cornerhouserealty.com",
    "OLIVIA WILSON",
    "Sebastion Johnson",
]


def test_the_sample_testimonial_is_recognised() -> None:
    """It opens "Review goes here...", so an anchored pattern missed it."""
    resolved = fields.resolve(CLIENT_REVIEW_TEXT)

    assert resolved.fields["review_quote"].startswith("Review goes here...")


def test_the_sample_agent_in_the_footer_is_recognised() -> None:
    """Otherwise this name prints on somebody else's testimonial flyer."""
    resolved = fields.resolve(CLIENT_REVIEW_TEXT)

    assert resolved.fields["agent_name"] == "Sebastion Johnson"


def test_the_reviewing_client_is_recognised() -> None:
    resolved = fields.resolve(CLIENT_REVIEW_TEXT)

    assert resolved.fields["client_name"] == "OLIVIA WILSON"


def test_the_section_heading_is_design_copy_not_a_field() -> None:
    """It stopped the run as a fillable field Gable could not identify."""
    resolved = fields.resolve(CLIENT_REVIEW_TEXT)
    filled = set(resolved.fields.values()) | {
        literal for extras in resolved.also.values() for literal in extras
    }

    assert "CLIENT TESTIMONIAL" not in filled
    assert "REAL PEOPLE\nREAL RESULTS" not in filled
    assert "Let’s Find Your Corner." not in filled  # noqa: RUF001


def test_every_real_person_on_the_design_is_replaced() -> None:
    """Nothing belonging to the sample may survive a fill."""
    resolved = fields.resolve(CLIENT_REVIEW_TEXT)
    pairs = fields.replacements(
        resolved,
        {
            "agent_name": "Andy Jang",
            "agent_phone": "410.218.2786",
            "agent_email": "andy@cornerhouserealty.com",
            "client_name": "Sarah Whitfield",
            "review_quote": "Andy made our first purchase painless.",
        },
    )

    assert pairs["Sebastion Johnson"] == "Andy Jang"
    assert pairs["443-499-3839"] == "410.218.2786"
    assert pairs["sebastian@cornerhouserealty.com"] == "andy@cornerhouserealty.com"
    assert pairs["OLIVIA WILSON"] == "Sarah Whitfield"


# --- reading the reviewer's name out of what an agent typed -----------------


def test_a_named_attribution_is_read_rather_than_asked_about() -> None:
    """"Sarah Whitfield said: ..." says whose words follow. That is not a guess."""
    from gable.listings.review import parse_review

    found = parse_review(
        "Sarah Whitfield said: Andy made our first purchase painless. He answered "
        "every question the same day and negotiated a price we did not think we could get."
    )

    assert found.client_name == "Sarah Whitfield"
    assert found.quote.startswith("Andy made our first purchase painless.")
    assert found.is_usable


def test_the_agent_being_praised_is_never_taken_as_the_reviewer() -> None:
    """"Gina was outstanding" names the agent, not the person reviewing her."""
    from gable.listings.review import parse_review

    found = parse_review(
        "In a simple word, Gina was outstanding! My sister needed to sell, but she "
        "suffers from dementia and the whole thing was handled with real care."
    )

    assert found.client_name == "", "a name in the body is not an attribution"


def test_the_name_on_its_own_line_still_works() -> None:
    """The shape the real submissions use."""
    from gable.listings.review import parse_review

    found = parse_review(
        "Google review for SRES listing, 29 Maple\nRob Morgan\n\n"
        "In a simple word, Gina was outstanding! My sister needed to sell and the "
        "whole thing was handled with real care from start to finish."
    )

    assert found.client_name == "Rob Morgan"
    assert found.quote.startswith("In a simple word")
