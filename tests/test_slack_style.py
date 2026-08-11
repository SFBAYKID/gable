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


# --- nothing Gable can build may break the rules ----------------------------


def test_no_block_kit_builder_emits_an_emoji() -> None:
    """blocks.py predates the guide and was full of them.

    Reads the module source rather than calling every builder, so a new one
    added later is covered without anyone remembering to test it.
    """
    import re
    from pathlib import Path

    import gable.slackapp.blocks as blocks_module

    source = Path(blocks_module.__file__).read_text(encoding="utf-8")
    emoji = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]")
    found = emoji.findall(source)
    assert not found, f"blocks.py still contains emoji: {found}"


def test_no_source_file_outside_the_style_module_contains_an_emoji() -> None:
    """The rule is repo-wide, not just for one file."""
    import re
    from pathlib import Path

    emoji = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]")
    root = Path(__file__).resolve().parent.parent / "src" / "gable"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "style.py":
            continue  # it defines the patterns, so it necessarily mentions them
        if emoji.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"emoji found in: {offenders}"


def test_every_block_builder_renders_a_clean_message() -> None:
    """Source-level checks miss code spans and bracketed placeholders.

    This one calls each builder and runs the rules over the actual strings a
    reader would see. It caught two backtick spans that the emoji scan did not:
    the tab name in the unknown-agent card, and the run id in the failure card.
    """
    from datetime import UTC, datetime

    from gable.models import AgentProfile, Listing, PhotoResult, PhotoSource
    from gable.slackapp import blocks

    listing = Listing(
        response_row_id="r1",
        submitted_at=datetime(2026, 8, 11, tzinfo=UTC),
        agent_email="lolo@cornerhouserealty.com",
        agent_name="Lolo Simmons",
        address="7940 Oakwood Rd, Glen Burnie, MD 21061",
        price_display="",
        agent_phone="(443) 854-8554",
    )
    agent = AgentProfile(
        agent_email="lolo@cornerhouserealty.com",
        agent_name="Lolo Simmons",
        template_label="Just Listed",
    )
    supplied = PhotoResult(
        source=PhotoSource.CARMEN, url="http://198.51.100.7/a.jpg", confidence=1.0
    )
    generated = PhotoResult(
        source=PhotoSource.GENERATED,
        url="http://198.51.100.7/b.jpg",
        confidence=1.0,
        ai_generated=True,
    )

    def rendered(built: list[dict]) -> list[str]:  # type: ignore[type-arg]
        out: list[str] = []
        for block in built:
            text = block.get("text", {})
            if isinstance(text, dict) and text.get("text"):
                out.append(str(text["text"]))
            for f in block.get("fields") or []:
                if f.get("text"):
                    out.append(str(f["text"]))
            for el in block.get("elements") or []:
                inner = el.get("text")
                if isinstance(inner, dict) and inner.get("text"):
                    out.append(str(inner["text"]))
                elif isinstance(inner, str):
                    out.append(inner)
        return out

    cases = {
        "listing_ready": blocks.listing_ready_blocks(listing, agent, supplied, "run1"),
        "needs_photo": blocks.needs_photo_blocks(listing, ("Drive folder",), None, 0.75, "run1"),
        "ai_generated": blocks.ai_generated_blocks(listing, generated, "run1"),
        "unknown_agent": blocks.unknown_agent_blocks(listing, ("Just Listed",), "run1"),
        "batch": blocks.batch_delivered_blocks(("123 Anywhere St", "456 Oak Ave"), 1),
        "failure": blocks.failure_blocks(listing.address, "Google refused that edit", "run1"),
    }
    problems: list[str] = []
    for name, built in cases.items():
        for text in rendered(built):
            for problem in violations(text):
                problems.append(f"{name}: {text[:60]!r} — {problem}")
    assert not problems, "\n".join(problems)


def test_no_card_repeats_an_action_id() -> None:
    """Slack rejects a whole message whose action ids are not unique.

    The unknown-agent card emitted the same id on every template button, so
    Slack answered invalid_blocks and the card could never be posted at all.
    Only discovered by posting it for real.
    """
    from datetime import UTC, datetime

    from gable.models import AgentProfile, Listing, PhotoResult, PhotoSource
    from gable.slackapp import blocks

    listing = Listing(
        response_row_id="r1",
        submitted_at=datetime(2026, 8, 11, tzinfo=UTC),
        agent_email="lolo@cornerhouserealty.com",
        agent_name="Lolo Simmons",
        address="7940 Oakwood Rd, Glen Burnie, MD 21061",
        price_display="",
    )
    agent = AgentProfile(
        agent_email="lolo@cornerhouserealty.com",
        agent_name="Lolo Simmons",
        template_label="Just Listed",
    )
    photo = PhotoResult(source=PhotoSource.CARMEN, url="http://198.51.100.7/a.jpg", confidence=1.0)

    cards = {
        "unknown_agent": blocks.unknown_agent_blocks(
            listing, ("Just Listed", "Just Sold", "Open House", "Coming Soon"), "run1"
        ),
        "listing_ready": blocks.listing_ready_blocks(listing, agent, photo, "run1"),
        "needs_photo": blocks.needs_photo_blocks(listing, ("Drive",), None, 0.75, "run1"),
        "failure": blocks.failure_blocks("1 Test St", "Google refused that edit", "run1"),
    }
    for name, built in cards.items():
        ids = [
            el["action_id"]
            for block in built
            for el in (block.get("elements") or [])
            if isinstance(el, dict) and "action_id" in el
        ]
        assert len(ids) == len(set(ids)), f"{name} repeats an action id: {ids}"


def test_dispatch_key_recovers_the_route_from_a_suffixed_id() -> None:
    """Handlers must route on the stable part, not the raw id."""
    from gable.slackapp.blocks import ACTION_PICK_TEMPLATE, dispatch_key

    assert dispatch_key(f"{ACTION_PICK_TEMPLATE}:2") == ACTION_PICK_TEMPLATE
    assert dispatch_key(ACTION_PICK_TEMPLATE) == ACTION_PICK_TEMPLATE


def test_button_labels_do_not_invite_emoji_expansion() -> None:
    """emoji:true would let Slack expand a shortcode inside a label."""
    from datetime import UTC, datetime

    from gable.models import Listing
    from gable.slackapp import blocks

    listing = Listing(
        response_row_id="r1",
        submitted_at=datetime(2026, 8, 11, tzinfo=UTC),
        agent_email="a@b.com",
        agent_name="A B",
        address="1 Test St",
        price_display="",
    )
    built = blocks.unknown_agent_blocks(listing, ("Just Listed",), "run1")
    for block in built:
        for el in block.get("elements") or []:
            if isinstance(el, dict) and el.get("type") == "button":
                assert el["text"]["emoji"] is False


# --- what actually arrives in an event ---------------------------------------


def test_a_mention_is_stripped_to_what_the_person_typed() -> None:
    from gable.slackapp.app import clean_mention_text

    assert clean_mention_text("<@U0BP624RK5H> hello") == "hello"


def test_a_mention_with_a_display_name_is_stripped_too() -> None:
    """Slack writes `<@U123|Gable>` when a message is read back."""
    from gable.slackapp.app import clean_mention_text

    assert clean_mention_text("<@U0BP624RK5H|Gable> hello") == "hello"


def test_a_client_footer_is_not_part_of_the_question() -> None:
    """Some clients append a footer. Feeding it to the model is noise."""
    from gable.slackapp.app import clean_mention_text

    assert clean_mention_text("<@U123ABC> hello\n*Sent using* Claude") == "hello"


def test_an_empty_mention_yields_an_empty_string() -> None:
    from gable.slackapp.app import clean_mention_text

    assert clean_mention_text("<@U123ABC>") == ""
    assert clean_mention_text("") == ""


def test_a_reply_that_breaks_the_rules_never_reaches_slack() -> None:
    """The last gate. A model told not to use emoji mostly will not."""
    from gable.slackapp.app import FALLBACK, safe_reply

    assert safe_reply("All set.") == "All set."
    assert "sparkles" not in safe_reply(":sparkles: All set.")
    assert safe_reply("<HttpError 400 unrecoverable>") == FALLBACK


def test_an_unexecuted_edit_is_never_announced_as_done() -> None:
    from gable.slackapp.app import describe_action, reply_for_decision
    from gable.slackapp.brain import Decision

    decision = Decision(
        reply="Making the price bigger.",
        tool="set_font_size",
        arguments={"target": "price", "points": 32},
    )

    assert describe_action(decision) == ""
    assert reply_for_decision(decision) == (
        "I understood the change, but I could not apply it. I have not changed the flyer."
    )


def test_a_clarifying_question_still_reaches_the_person() -> None:
    from gable.slackapp.app import reply_for_decision
    from gable.slackapp.brain import Decision

    decision = Decision(
        reply="Did you mean the large photo or the headshot?",
        tool="ask_clarifying",
        arguments={"question": "Did you mean the large photo or the headshot?"},
    )

    assert reply_for_decision(decision) == decision.reply
