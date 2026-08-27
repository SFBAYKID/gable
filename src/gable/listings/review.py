"""Pulling a client review out of what an agent typed into the form.

A review post has no property. It has a quote and the name of the person who
gave it, and both arrive as prose in the submission's post-details field, in
whatever shape the agent felt like typing.

The real one on row 5 looks like this:

    Google review for SRES listing, 29 Maple
    Rob Morgan

    In a simple word, Gina was outstanding! My sister needed to sell, but she
    suffers from dementia...

A context line, the reviewer's name on its own, a blank line, then the review.
That shape is common enough to read, and when it cannot be read the answer is to
ask rather than to guess — putting the wrong name under a stranger's words about
their own family is worse than an unfinished flyer.

Does not handle: deciding whether a submission *is* a review. That is the
request type, and `intake.category` already knows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: A line that is a person's name and nothing else: two or three capitalised
#: words, no sentence punctuation. "Rob Morgan" matches; "In a simple word, Gina
#: was outstanding!" does not.
_NAME_LINE: Final[re.Pattern[str]] = re.compile(
    # \u2019 is the curly apostrophe, spelled as an escape because a literal one
    # is indistinguishable from a backtick in a character class.
    "^[A-Z][a-zA-Z'\u2019\\-]+(?:\\s+[A-Z][a-zA-Z'\u2019\\-]+){1,2}$"
)

#: "Sarah Whitfield said: ..." — the reviewer named inline, followed by their
#: words. Unambiguous in a way that "Gina was outstanding" is not, because the
#: sentence itself says whose words follow.
_ATTRIBUTION: Final[re.Pattern[str]] = re.compile(
    # \u2019 spelled as an escape, for the reason given on _NAME_LINE above.
    "^(?P<name>[A-Z][a-zA-Z'\u2019\\-]+(?:\\s+[A-Z][a-zA-Z'\u2019\\-]+){1,2})"
    r"\s+(?:said|wrote|says)\s*[:,-]?\s*(?P<quote>.+)$",
    re.DOTALL,
)

#: A reviewer signing off at the end: "Sharon", "-Sharon", "— Rob Morgan".
#:
#: One to three capitalised words on the last line, no sentence punctuation,
#: optionally introduced by a dash. A SINGLE word counts here and deliberately
#: does not count for `_NAME_LINE` above: at the top of a submission one
#: capitalised word is far more likely to be a heading ("Testimonial",
#: "Review"), while at the end, after a long quote, it is a signature.
#:
#: Measured against the five real Client Review submissions on 2026-08-27. Two
#: of them sign off this way and neither could be read: row 130 ends " Sharon"
#: and row 39 ends "Yvette". Both were asked about in Slack, and row 130 was
#: asked for a quote AND a name the form had already carried all along.
_TRAILING_NAME: Final[re.Pattern[str]] = re.compile(
    # \u2019 spelled as an escape, for the reason given on _NAME_LINE above.
    "^[-\u2013\u2014]?\\s*"
    "(?P<name>[A-Z][a-zA-Z'\u2019\\-]+(?:\\s+[A-Z][a-zA-Z'\u2019\\-]+){0,2})$"
)

#: Quotation marks a submission wraps its review in. The designs draw their own
#: quote glyphs, so a pasted pair renders as a second set inside the first.
_WRAPPING_QUOTES: Final[str] = "\"\u201c\u201d'\u2018\u2019"

#: Below this a "review" is a fragment rather than something worth setting in a
#: quote panel.
_MIN_QUOTE_CHARS: Final[int] = 40


@dataclass(frozen=True, slots=True)
class Review:
    """A client's words and their name, as far as they could be read."""

    client_name: str = ""
    quote: str = ""

    @property
    def is_usable(self) -> bool:
        """True when both halves are present. Either alone is a question."""
        return bool(self.client_name and len(self.quote) >= _MIN_QUOTE_CHARS)


def review_values(request_type: str, text: str) -> dict[str, str]:
    """The client's words and name, on a review post only.

    Args:
        request_type: The submission's request type.
        text: The prose the agent typed.

    Returns:
        `client_name` and `review_quote`, or an empty mapping when this is not a
        review or the prose could not be read. Empty leaves the design's sample
        testimonial visible, which becomes a question rather than a flyer
        quoting the wrong person.

    Raises:
        Nothing.
    """
    if "review" not in request_type.lower():
        return {}
    found = parse_review(text)
    if not found.is_usable:
        return {}
    return {"client_name": found.client_name, "review_quote": found.quote}


def parse_review(text: str) -> Review:
    """Read a reviewer's name and their words out of free prose.

    Args:
        text: The submission's post details, as typed.

    Returns:
        A `Review`. Missing halves come back empty rather than guessed, and
        `is_usable` is what the caller should branch on.

    Raises:
        Nothing.

    Note:
        The name is taken from a line that is *only* a name, which is how these
        submissions are written. Taking the first capitalised words in the body
        would find the agent being praised — "Gina was outstanding" — and put the
        agent's name where the client's belongs.
    """
    # "Sarah Whitfield said: ..." is the one inline shape that names the
    # reviewer without ambiguity — the words after it are explicitly hers, so
    # reading it is not a guess. Anything vaguer still falls through to the
    # name-on-its-own-line rule and then to a question.
    attributed = _ATTRIBUTION.match(text.strip())
    if attributed:
        quote = " ".join(attributed.group("quote").split()).strip().strip('"“”')
        return Review(client_name=attributed.group("name").strip(), quote=quote)

    lines = [line.strip() for line in text.splitlines()]
    name = ""
    name_index = -1
    for index, line in enumerate(lines):
        if line and _NAME_LINE.match(line):
            name, name_index = line, index
            break

    # The review is whatever follows the name line. Without a name line, treat
    # the longest paragraph as the quote and leave the name to be asked about.
    if name_index >= 0:
        body = " ".join(part for part in lines[name_index + 1 :] if part).strip()
        return Review(client_name=name, quote=_unwrapped(body))

    # The quote is the longest paragraph, never the whole submission. Row 130
    # opens with a "Five star review" header on its own; joining every line
    # above the signature printed that header inside the testimonial.
    body = max(text.split("\n\n"), key=len, default="").strip()

    # Nobody named themselves at the top, so look at the bottom of that
    # paragraph: two of the five real submissions sign off there. Only trailing
    # text that is purely a short name qualifies, and only when a substantial
    # quote precedes it, so the last sentence of an unsigned review can never be
    # mistaken for a signature.
    body_lines = [line.strip() for line in body.splitlines() if line.strip()]
    if body_lines:
        signature = _TRAILING_NAME.match(body_lines[-1])
        if signature:
            above = _unwrapped(" ".join(body_lines[:-1]))
            if len(above) >= _MIN_QUOTE_CHARS:
                return Review(client_name=signature.group("name").strip(), quote=above)

    return Review(client_name=name, quote=_unwrapped(body))


def _unwrapped(quote: str) -> str:
    """Strip one pair of quotation marks the submission wrapped a review in.

    Args:
        quote: The review text as it was typed.

    Returns:
        The same text without a matched surrounding pair. These designs draw
        their own opening and closing quote glyphs, so a pasted pair renders as
        a second set inside the first. Only a MATCHED pair is removed: a review
        that merely ends in a quoted phrase keeps every mark it has.

    Raises:
        Nothing.
    """
    text = " ".join(quote.split()).strip()
    if len(text) >= 2 and text[0] in _WRAPPING_QUOTES and text[-1] in _WRAPPING_QUOTES:
        return text[1:-1].strip()
    return text
