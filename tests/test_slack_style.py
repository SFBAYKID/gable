"""Tests for the Slack house style.

Every example under "the real violations" is copied from a message Gable
actually posted before the guide existed, so these are regression tests against
things that shipped, not invented ones.
"""

from __future__ import annotations

import pytest

from gable.slackapp.style import (
    humanize_error,
    is_clean,
    link,
    missing_fields_sentence,
    quote_rail,
    strip_to_plain,
    violations,
)

# --- the real violations ----------------------------------------------------


def test_emoji_shortcodes_are_caught() -> None:
    assert not is_clean(":white_check_mark:  Done.")
    assert "emoji" in violations(":white_check_mark: Done.")[0]


def test_unicode_emoji_are_caught() -> None:
    for bad in ("🏡 New request", "✅ Done", "❌ Failed", "✨ Your flyer"):
        assert not is_clean(bad), bad


def test_bracket_tokens_are_caught() -> None:
    """`[PRICE] · [ 4 BEDS ]` shipped in red monospace. Never again."""
    assert not is_clean("[PRICE] and [ 4 BEDS ] are still placeholders")
    assert any("bracketed" in v for v in violations("[PRICE]"))


def test_placeholder_tokens_are_caught() -> None:
    assert not is_clean("I left {{price}} alone")


def test_a_raw_http_error_is_caught() -> None:
    """This exact string reached Carmen's thread."""
    bad = "reduce the padding — <HttpError 400 when requesting https://slides.googleapis.com/v1>"
    assert not is_clean(bad)


def test_code_spans_are_caught() -> None:
    assert not is_clean("Here you go — `download.jpg`")
    assert any("code styling" in v for v in violations("`download.jpg`"))


def test_stage_directions_are_caught() -> None:
    assert not is_clean("(simulating Carmen)  Here you go")


def test_a_pasted_url_is_caught() -> None:
    assert not is_clean("https://docs.google.com/presentation/d/18k9sK0lf2rZQ8/edit")


def test_several_violations_are_all_reported() -> None:
    """One message, several problems — report them all, not just the first."""
    bad = ":x: `download.jpg` failed — <HttpError 400> https://example.test/a"
    assert len(violations(bad)) >= 4


# --- what good looks like ---------------------------------------------------


def test_the_canonical_message_from_the_mockup_passes() -> None:
    """Copied from the locked mockup thread, which is the reference."""
    good = (
        "*New request from Lolo Simmons*\n"
        "&gt;*7940 Oakwood Rd, Glen Burnie, MD 21061*\n"
        "&gt;Template  *Just Listed*\n"
        "&gt;Agent  Lolo Simmons · (443) 854-8554\n\n"
        "I've got everything except the photo. Which image do you want as the hero?"
    )
    assert is_clean(good), violations(good)


def test_a_proper_link_passes() -> None:
    message = f"Your flyer is ready. {link('https://docs.google.com/x', 'Open the flyer')}"
    assert is_clean(message), violations(message)


def test_link_needs_words_not_just_a_url() -> None:
    with pytest.raises(ValueError, match="both a URL and the words"):
        link("https://x.test", "  ")


def test_quote_rail_formats_facts() -> None:
    rail = quote_rail(["7940 Oakwood Rd", "Template  Just Listed", ""])
    assert rail == "&gt;7940 Oakwood Rd\n&gt;Template  Just Listed"
    assert is_clean(rail)


def test_an_empty_quote_rail_is_empty_not_a_stray_marker() -> None:
    assert quote_rail(["", "   "]) == ""


# --- missing fields read as a sentence --------------------------------------


def test_missing_fields_become_a_sentence_not_tokens() -> None:
    """The guide's own example, and the whole point of the rule."""
    sentence = missing_fields_sentence(["price", "beds", "baths", "square footage"])
    assert sentence.startswith("Price, beds, baths and square footage aren't")
    assert is_clean(sentence)
    assert "[" not in sentence


def test_a_single_missing_field_reads_naturally() -> None:
    sentence = missing_fields_sentence(["price"])
    assert "Price isn't" in sentence
    assert " it off" in sentence


def test_nothing_missing_says_nothing() -> None:
    assert missing_fields_sentence([]) == ""
    assert missing_fields_sentence(["  "]) == ""


# --- errors become plain language -------------------------------------------


def test_the_padding_failure_reads_as_english() -> None:
    """The real one: Slides rejected leftInset as an unknown field."""
    said = humanize_error(
        'Invalid JSON payload received. Unknown name "leftInset"',
        "tightening the address box padding",
    )
    assert is_clean(said), violations(said)
    assert "leftInset" not in said
    assert "tightening the address box padding" in said
    assert "doesn't allow that change" in said


def test_the_line_failure_reads_as_english() -> None:
    """The other real one: the divider is a Line, not a Shape."""
    said = humanize_error(
        "Invalid requests[0].updateShapeProperties: The object (p1_i28) is not of type SHAPE.",
        "recolouring the middle line",
    )
    assert is_clean(said), violations(said)
    assert "p1_i28" not in said
    assert "line rather than a shape" in said


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("429 rate limit exceeded", "slow down"),
        ("File not found: abc", "couldn't find that file"),
        ("The caller does not have permission", "don't have access"),
        ("Read timed out", "took too long"),
        ("There was a problem retrieving the image", "couldn't fetch the photo"),
    ],
)
def test_known_causes_are_translated(raw: str, expected: str) -> None:
    said = humanize_error(raw, "doing that")
    assert expected in said
    assert is_clean(said)


def test_an_unrecognised_error_still_never_leaks() -> None:
    """The fallback matters more than the known cases — it is the default."""
    said = humanize_error("PANIC: goroutine 1 [running]: 0xdeadbeef", "saving the flyer")
    assert is_clean(said), violations(said)
    assert "0xdeadbeef" not in said
    assert "goroutine" not in said


def test_every_humanized_error_offers_a_way_forward() -> None:
    for raw in ("400 invalid", "unknown", "429", "timeout"):
        assert "try again" in humanize_error(raw, "that")


# --- the last-resort scrub --------------------------------------------------


def test_scrub_rescues_a_careless_message() -> None:
    scrubbed = strip_to_plain(":sparkles: `download.jpg` and [PRICE] (simulating Carmen)")
    assert is_clean(scrubbed), violations(scrubbed)
    assert "download.jpg" in scrubbed
    assert "PRICE" in scrubbed


def test_scrub_leaves_a_clean_message_alone() -> None:
    good = "Your flyer is ready. Tell me what to change and I'll do it here."
    assert strip_to_plain(good) == good


def test_scrub_keeps_a_real_link_intact() -> None:
    good = f"Ready. {link('https://docs.google.com/x', 'Open the flyer')}"
    assert strip_to_plain(good) == good


# --- what Slack does to our text on the way back ----------------------------


def test_slacks_own_phone_link_is_not_a_violation() -> None:
    """Slack rewrites a phone number into a tel: link when read back.

    Auditing posted messages would otherwise flag our own clean text as
    containing angle brackets.
    """
    as_posted = "Agent  Lolo Simmons · (443) 854-8554"
    as_read_back = "Agent  Lolo Simmons · <tel:(443)854-8554|(443) 854-8554>"
    assert is_clean(as_posted)
    assert is_clean(as_read_back), violations(as_read_back)


def test_slacks_own_mailto_link_is_not_a_violation() -> None:
    read_back = "Reach her at <mailto:lolo@cornerhouserealty.com|lolo@cornerhouserealty.com>"
    assert is_clean(read_back), violations(read_back)


def test_a_genuine_raw_error_in_angle_brackets_is_still_caught() -> None:
    """The relaxation must not open the door the rule exists to close."""
    assert not is_clean("failed — <HttpError 400 when requesting the API>")
