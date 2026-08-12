"""What a reply must look like by the time it reaches Slack.

Slack does not render Markdown. `**bold**` shows the asterisks and `## Heading`
shows the hashes, so a message written in Markdown arrives looking broken rather
than formatted. The model produced exactly that on 2026-08-12 — headed sections
and bulleted lists, hundreds of words long — which is what these guard.
"""

from __future__ import annotations

from gable.voice import MAX_REPLY_CHARS, for_slack, safe, shorten


def test_markdown_bold_becomes_slack_bold() -> None:
    """`**x**` shows its asterisks in Slack; `*x*` is bold."""
    assert for_slack("I need the **address** and the **price**.") == (
        "I need the *address* and the *price*."
    )


def test_headings_lose_their_hashes() -> None:
    """Slack has no headings, so the hashes just render as hashes."""
    assert for_slack("## Required\nThe address.") == "Required\nThe address."


def test_markdown_bullets_become_real_bullets() -> None:
    """A leading hyphen reads as a dash mid-sentence, not as a list."""
    assert for_slack("- address\n- price") == "• address\n• price"
    assert for_slack("1. address\n2. price") == "• address\n• price"


def test_a_plain_sentence_is_left_alone() -> None:
    """The common case must pass through untouched."""
    plain = "I need the address and the sold price. Send me a photo when you can."
    assert for_slack(plain) == plain


def test_a_long_reply_is_cut_at_a_sentence() -> None:
    """Truncating mid-word looks broken; cutting at a sentence reads as brief."""
    long_reply = ("I need the address. " * 80).strip()
    cut = shorten(long_reply)
    assert len(cut) <= MAX_REPLY_CHARS
    assert cut.endswith(".")


def test_a_short_reply_is_not_cut() -> None:
    """Most replies are already short and must not be touched."""
    short = "I need the address and the sold price."
    assert shorten(short) == short


def test_safe_applies_formatting_and_length_together() -> None:
    """`safe` is the one call every module makes before speaking."""
    wordy = "## What I need\n" + "- The **address**. " * 60
    out = safe(wordy)
    assert "**" not in out
    assert "##" not in out
    assert len(out) <= MAX_REPLY_CHARS


def test_the_wall_of_text_from_the_live_test_is_contained() -> None:
    """The real reply that prompted this: headed, bulleted, hundreds of words."""
    real = (
        "Here's what I need to build a Just Sold flyer.\n\n"
        "**Required from you**\n"
        "- Property address (full street, city, ZIP)\n"
        "- Final sold price (and closing date, if you want it shown)\n"
        "- Hero photo (tell me which image to use)\n"
        "- Agent name as it should appear on the flyer\n"
        "- Agent headshot image\n"
        "- Agent phone and email\n"
        "- Brokerage name and logo\n\n"
        "**I will look up for you**\n"
        "- Beds / baths / square footage / year built / lot size / days on market\n"
    )
    out = safe(real)
    assert "**" not in out, "Slack shows raw asterisks"
    assert "- " not in out, "hyphens are not list markers in Slack"
    assert len(out) <= MAX_REPLY_CHARS
