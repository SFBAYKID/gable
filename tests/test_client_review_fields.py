"""Reading the Client Review Post design.

Every text run on the live design, read 2026-08-14. Three of its pieces were
unrecognised: the section heading was reported as an unnamed fillable field and
stopped the run, and the sample review and the sample agent in the footer would
both have survived onto a real agent's flyer.
"""

from __future__ import annotations

from gable.listings.review import review_values
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
    """An attribution line says whose words follow, so reading it is not a guess."""
    from gable.listings.review import parse_review

    found = parse_review(
        "Sarah Whitfield said: Andy made our first purchase painless. He answered "
        "every question the same day and negotiated a price we did not think we could get."
    )

    assert found.client_name == "Sarah Whitfield"
    assert found.quote.startswith("Andy made our first purchase painless.")
    assert found.is_usable


def test_the_agent_being_praised_is_never_taken_as_the_reviewer() -> None:
    """Praise in the body names the agent, never the person reviewing her."""
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


def test_a_client_review_manifest_asks_for_no_property_address() -> None:
    """Row 5 was asked to separate the ZIP of "Google Review, SRES Listing 29 Maple"."""
    from gable.slides import manifest as template_manifest

    found = template_manifest.manifest_for("Client Review Post")

    assert found.find("address") is None
    assert found.find("price") is None
    agent = found.find("agent_name")
    assert agent is not None and agent.required


def test_a_client_review_with_no_address_validates_clean() -> None:
    from gable.slides import manifest as template_manifest

    found = template_manifest.manifest_for("Client Review Post")

    problems = template_manifest.validate(
        found,
        {
            "agent_name": "Gina Moore",
            "agent_phone": "410.299.6536",
            "agent_email": "gina@cornerhouserealty.com",
            "hero_photo": "http://photos.example/house.jpg",
        },
    )

    assert problems == []


def test_a_listing_design_still_demands_a_whole_address() -> None:
    from gable.slides import manifest as template_manifest

    found = template_manifest.manifest_for("Open House")

    problems = template_manifest.validate(
        found,
        {
            "address": "1011 Winged Foot Drive",
            "agent_name": "Tambria Eaton",
            "agent_phone": "443.739.7534",
            "hero_photo": "http://photos.example/house.jpg",
        },
    )

    assert [item.field_name for item in problems] == ["address"]


# --- the shapes real submissions actually arrive in -------------------------
#
# Read from the live workbook 2026-08-27: five Client Review Posts have ever
# been submitted, in five different layouts, and the parser read exactly one of
# them. The other four each cost Carmen a round trip in Slack for a value the
# form was already carrying. Row 130 cost her two, and was the listing that
# spent the afternoon being asked for a property photo as well.


def test_a_one_word_signature_at_the_end_is_the_client() -> None:
    """Row 130, Porsher Howard's. The form carried the quote AND the name.

    Gable asked Carmen for both. `_NAME_LINE` needs two capitalised words, so
    " Sharon" on the last line was invisible and the whole submission fell to
    "longest paragraph, no name" — which is not usable, so it became a question.
    """
    values = review_values(
        "Client Review Post",
        "Five star review\n\n\"Porsher Howard was the best of all of the realtors I've "
        "dealt with over the years. She was friendly, courteous and efficient in her "
        "communication skills. She made our home buying experience so much easier and "
        'enjoyable. I feel like I have a new friend in this world thanks to Porsher."\n'
        " Sharon",
    )

    assert values["client_name"] == "Sharon"
    # The header is not part of the testimonial, and neither are the quotation
    # marks: this design draws its own opening and closing glyphs.
    assert values["review_quote"].startswith("Porsher Howard was the best")
    assert "Five star review" not in values["review_quote"]
    assert '"' not in values["review_quote"]


def test_a_signature_with_no_blank_line_before_it_is_still_the_client() -> None:
    """Row 39, Kim Hixson's. One paragraph, the name simply last."""
    values = review_values(
        "Client Review Post",
        "Kim is amazing!! She is personal and down to earth, which is something I "
        "really loved about working with her. She helped my family find our forever "
        "home and worked with our special circumstances in mind. I would highly "
        "recommend Kim and her amazing team.\nYvette",
    )

    assert values["client_name"] == "Yvette"
    assert values["review_quote"].startswith("Kim is amazing")
    assert "Yvette" not in values["review_quote"]


def test_a_dashed_signature_is_read_the_way_carmen_types_it() -> None:
    """Carmen signs quotes "-Sharon" in Slack; agents do the same on the form."""
    body = (
        "She answered every question the same day and never made us feel rushed. "
        "We got the house we actually wanted."
    )
    # Hyphen, em dash and en dash, spelled as escapes so the dashes are
    # unambiguous in source.
    for signature in ("-Dana", "\u2014 Dana", "\u2013 Dana", "Dana"):
        values = review_values("Client Review Post", f"{body}\n{signature}")
        assert values["client_name"] == "Dana", signature
        assert "Dana" not in values["review_quote"], signature


def test_a_name_line_at_the_top_still_wins() -> None:
    """Row 5, Gina Moore's — the one shape that always worked. Unchanged."""
    values = review_values(
        "Client Review Post",
        "Google review for SRES listing, 29 Maple \nRob Morgan\n\n"
        "In a simple word, Gina was outstanding! My sister needed to sell, but she "
        "suffers from dementia. She went the extra mile to make it painless.",
    )

    assert values["client_name"] == "Rob Morgan"
    assert values["review_quote"].startswith("In a simple word")


def test_an_unsigned_review_is_still_a_question() -> None:
    """The last sentence of a review must never be mistaken for a signature.

    Rows 49 and 50 are Google exports headed "7/2/2026 • lucyglou". Their last
    line is the review itself, and a username is not a name to print under a
    stranger's words, so both still ask. That is the correct outcome.
    """
    values = review_values(
        "Client Review Post",
        "7/2/2026 • lucyglou\nBought a Townhouse home in 2026.\n\n"
        "We recently bought a house with Ian and the entire time working with him "
        "the deal was very pleasant. Highly recommend Ian as your agent!",
    )

    assert values == {}


def test_a_trailing_name_without_a_real_quote_is_not_a_review() -> None:
    """A signature under a fragment is not something to set in a quote panel."""
    assert review_values("Client Review Post", "Loved it!\nSharon") == {}
