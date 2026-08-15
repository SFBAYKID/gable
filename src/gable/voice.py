"""What Gable is allowed to say, enforced rather than remembered.

`AGENTS.md` defines the house style. A style guide nobody can run is a style
guide that drifts, so this module makes it executable: `violations()` is the
rule set, and the helpers below produce text that passes it.

The rules exist for a reason worth restating, because they look cosmetic and are
not. Carmen is a designer, not an operator. A message reading

    :x: HttpError 400: Invalid requests[0].updateShapeProperties: leftInset

tells her nothing she can act on and costs her the confidence to trust the next
message. The same fact said plainly — *"Google Slides wouldn't take the padding
change. I can retry it."* — is shorter, truer, and leaves her able to decide.

So: no emoji, no brackets, no code spans, no raw exception text, no stage
directions, no pasted URLs. Facts in a quote rail, the question in plain body
text underneath.

This lives in the core rather than under `slackapp/` because it is a property
of Gable, not of Slack: CLAUDE.md §6 forbids anything in `src/gable/` importing
from `slackapp/`, and the pipeline needs these rules too. `slackapp.style`
re-exports it so existing imports keep working.

Does not handle: posting to Slack.
"""

from __future__ import annotations

import re
from typing import Final

#: Slack shortcodes like `:white_check_mark:`, and the Unicode ranges that carry
#: pictographs. Both are emoji as far as a reader is concerned.
_SHORTCODE: Final[re.Pattern[str]] = re.compile(r":[a-z0-9_+-]{2,}:")
_PICTOGRAPH: Final[re.Pattern[str]] = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff⬀-⯿️]"
)

#: Square brackets carry placeholder tokens; angle brackets carry raw errors.
#: Slack's own link syntax `<url|text>` is handled before this runs.
_SQUARE: Final[re.Pattern[str]] = re.compile(r"\[[^\]]*\]")
_ANGLE: Final[re.Pattern[str]] = re.compile(r"<[^>|]*>")
_CURLY: Final[re.Pattern[str]] = re.compile(r"\{\{[^}]*\}\}")

#: Backticks render as red monospace in Slack, which the guide forbids outright.
_CODE_SPAN: Final[re.Pattern[str]] = re.compile(r"`[^`]+`")

#: Fragments that only ever appear when an exception has leaked through.
_RAW_ERROR: Final[re.Pattern[str]] = re.compile(
    r"\b(HttpError|Traceback|Exception|TypeError|ValueError|KeyError|"
    r"AttributeError|returned \"|status_code|stacktrace|errno)\b",
    re.IGNORECASE,
)

#: "(simulating Carmen)" and friends — fine in a rehearsal, never in production.
_STAGE_DIRECTION: Final[re.Pattern[str]] = re.compile(
    r"\((simulating|pretending|mock|test mode)[^)]*\)", re.IGNORECASE
)

#: A bare URL sitting in the text rather than behind descriptive words.
_BARE_URL: Final[re.Pattern[str]] = re.compile(r"(?<![<|])https?://\S+")


def violations(text: str) -> list[str]:
    """Every house-style rule a message breaks.

    Args:
        text: The message exactly as it would be posted.

    Returns:
        One plain-language sentence per rule broken, empty if the message is
        clean. Returned rather than raised so a caller can log the message and
        fall back to something safe instead of dropping Carmen's reply entirely.

    Raises:
        Nothing.
    """
    found: list[str] = []

    # Strip Slack's own link syntax first: <url|words> is correct, not a violation.
    # This also has to cover the links Slack CREATES on our behalf — it turns a
    # phone number into <tel:4438548554|(443) 854-8554> and an address into a
    # mailto link when the message is read back, so auditing what was actually
    # posted would otherwise flag our own clean text.
    without_links = re.sub(r"<(?:https?|tel|mailto):[^>]*\|([^>]*)>", r"\1", text)
    without_links = re.sub(r"<(?:https?|tel|mailto):[^>|]*>", "", without_links)

    if _SHORTCODE.search(without_links) or _PICTOGRAPH.search(without_links):
        found.append("uses an emoji; status is shown with words or a small glyph")
    if _SQUARE.search(without_links):
        found.append("shows a bracketed token; write the field name as a word")
    if _CURLY.search(without_links):
        found.append("shows a placeholder token; write it as a word")
    if _ANGLE.search(without_links):
        found.append("contains angle brackets, which usually means a raw error leaked")
    if _CODE_SPAN.search(without_links):
        found.append("uses code styling; file and field names are written as plain text")
    if _RAW_ERROR.search(without_links):
        found.append("contains raw error text; say what happened in plain words")
    if _STAGE_DIRECTION.search(without_links):
        found.append("contains a stage direction")
    if _BARE_URL.search(without_links):
        found.append("pastes a URL; link descriptive words instead")
    return found


def is_clean(text: str) -> bool:
    """Whether a message may be posted as-is.

    Args:
        text: The candidate message.

    Returns:
        True when it breaks no rule.

    Raises:
        Nothing.
    """
    return not violations(text)


def link(url: str, words: str) -> str:
    """A Slack link behind descriptive words rather than a pasted address.

    Args:
        url: Where it points.
        words: What the reader sees, e.g. `Open the flyer`.

    Returns:
        Slack link markup.

    Raises:
        ValueError: if either argument is empty, since a link with no words is
            just a pasted URL with extra steps.
    """
    if not url.strip() or not words.strip():
        msg = "a link needs both a URL and the words to show"
        raise ValueError(msg)
    return f"<{url.strip()}|{words.strip()}>"


def quote_rail(lines: list[str]) -> str:
    """Facts in a quote-rail block, one per line.

    Args:
        lines: The facts. Empty lines are dropped.

    Returns:
        Slack blockquote markup, or an empty string if nothing survived.

    Raises:
        Nothing.
    """
    kept = [line.strip() for line in lines if line.strip()]
    return "\n".join(f"&gt;{line}" for line in kept)


def missing_fields_sentence(fields: list[str]) -> str:
    """Say what is absent as a sentence, never as visible tokens.

    The guide's own example: *"Price, beds, baths and square footage aren't on
    the form yet, so I left them off."*

    Args:
        fields: Field names as plain words, e.g. `["price", "beds"]`.

    Returns:
        A sentence, or an empty string when nothing is missing.

    Raises:
        Nothing.
    """
    kept = [f.strip() for f in fields if f.strip()]
    if not kept:
        return ""
    if len(kept) == 1:
        listed = kept[0]
        verb = "isn't"
    else:
        listed = ", ".join(kept[:-1]) + f" and {kept[-1]}"
        verb = "aren't"
    return (
        f"{listed.capitalize()} {verb} on your request form yet, so I left "
        f"{'it' if len(kept) == 1 else 'them'} off rather than guessing."
    )


#: Signatures we can recognise, mapped to what actually went wrong. Anything
#: unrecognised still gets a plain sentence — never the original string.
_KNOWN_CAUSES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"not of type SHAPE", re.I), "that piece is a line rather than a shape"),
    (
        re.compile(r"Unknown name|Cannot find field", re.I),
        "Google Slides doesn't allow that change",
    ),
    (re.compile(r"rate limit|quota|429", re.I), "Google asked me to slow down"),
    (re.compile(r"not found|404", re.I), "I couldn't find that file"),
    (re.compile(r"permission|403", re.I), "I don't have access to that"),
    (re.compile(r"timed out|timeout", re.I), "it took too long to respond"),
    (
        re.compile(r"retrieving the image|image was not found", re.I),
        "Google couldn't fetch the photo",
    ),
    (re.compile(r"invalid|400", re.I), "Google Slides refused that edit"),
)


def humanize_error(raw: str, what_failed: str) -> str:
    """Turn an exception into something Carmen can act on.

    Args:
        raw: The original error text. Never reaches the reader.
        what_failed: What was being attempted, in plain words, e.g.
            `tightening the address box padding`.

    Returns:
        One sentence naming the attempt and the cause, with no raw text in it.

    Raises:
        Nothing. This runs on the failure path; it must not add a second failure.
    """
    cause = "something went wrong at Google's end"
    for pattern, plain in _KNOWN_CAUSES:
        if pattern.search(raw or ""):
            cause = plain
            break
    return f"I couldn't finish {what_failed.strip()} — {cause}. I can try again."


def strip_to_plain(text: str) -> str:
    """Last-resort scrub for a message assembled somewhere careless.

    Removes emoji, code styling and bracketed tokens rather than letting a
    non-compliant message reach Carmen. A message that needs this has a bug
    behind it, so callers should log when it changes anything.

    Args:
        text: The candidate message.

    Returns:
        The message with the forbidden constructs removed and spacing tidied.

    Raises:
        Nothing.
    """
    out = _SHORTCODE.sub("", text)
    out = _PICTOGRAPH.sub("", out)
    out = _CODE_SPAN.sub(lambda m: m.group(0).strip("`"), out)
    out = _SQUARE.sub(lambda m: m.group(0).strip("[]").strip(), out)
    out = _CURLY.sub(lambda m: m.group(0).strip("{}").strip(), out)
    out = _STAGE_DIRECTION.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return "\n".join(line.strip() for line in out.splitlines()).strip()


#: Beyond this a Slack message stops being read and starts being skimmed. The
#: number is a ceiling, not a target — most replies should be far shorter.
MAX_REPLY_CHARS: Final[int] = 600

#: Markdown that Slack does not render. `**bold**` shows the asterisks, and a
#: `#` heading shows the hash, so both look like a mistake rather than emphasis.
_MD_BOLD: Final[re.Pattern[str]] = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MD_HEADING: Final[re.Pattern[str]] = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_BULLET: Final[re.Pattern[str]] = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_NUMBER: Final[re.Pattern[str]] = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_BLANK_RUN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")


def for_slack(text: str) -> str:
    """Render a reply the way Slack actually formats text.

    Args:
        text: A candidate message, possibly carrying Markdown.

    Returns:
        The same message in Slack's mrkdwn: `**bold**` becomes `*bold*`, headings
        lose their hashes, and list markers become bullet characters. Slack does
        not render Markdown, so leaving it in shows the raw asterisks and hashes
        to Carmen, which reads as a broken message rather than a formatted one.

    Raises:
        Nothing.
    """
    out = _MD_BOLD.sub(r"*\1*", text)
    out = _MD_HEADING.sub("", out)
    out = _MD_BULLET.sub("• ", out)
    out = _MD_NUMBER.sub("• ", out)
    out = _BLANK_RUN.sub("\n\n", out)
    return "\n".join(line.rstrip() for line in out.splitlines()).strip()


def shorten(text: str, limit: int = MAX_REPLY_CHARS) -> str:
    """Cut a runaway reply back to something a person will actually read.

    Args:
        text: The message.
        limit: Character ceiling.

    Returns:
        The message unchanged when it is already short enough, otherwise its
        first whole sentences up to the limit. Cutting at a sentence rather than
        a character keeps the result readable instead of truncated mid-word.

    Raises:
        Nothing.

    Note:
        This is a backstop, not the mechanism. The reply should be short because
        it was written short; arriving here means the wording needs fixing.
    """
    if len(text) <= limit:
        return text
    kept: list[str] = []
    total = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if total + len(sentence) > limit:
            break
        kept.append(sentence)
        total += len(sentence) + 1
    if not kept:
        return text[:limit].rsplit(" ", 1)[0]
    return " ".join(kept)


def paragraphs(*parts: str) -> str:
    """Join what Gable has to say as separate paragraphs, not one block.

    Chase, 2026-08-15, reading a refusal that ran two sentences together: "His
    writing needs to be not one big block." A finding, what Gable did about it,
    and what happens next are three different thoughts, and a reader takes them
    in far faster with air between them than packed into a paragraph.

    Args:
        *parts: The things to say, in order. Blank ones are dropped, so a
            caller can pass an optional note without guarding it.

    Returns:
        The parts separated by a blank line, each already trimmed.

    Raises:
        Nothing.
    """
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def safe(text: str) -> str:
    """Return `text` if it obeys the house style, or the closest thing that does.

    The one call any module can make before speaking.

    Args:
        text: A candidate message.

    Returns:
        The text unchanged when clean, a scrubbed version when scrubbing is
        enough, and a plain fallback when it is not.

    Raises:
        Nothing.
    """
    formatted = shorten(for_slack(text))
    if is_clean(formatted):
        return formatted
    scrubbed = strip_to_plain(formatted)
    if is_clean(scrubbed):
        return scrubbed
    return "I have an update on this one, but I could not word it properly. Ask me again."
