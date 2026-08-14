"""Build substring-safe Google Slides text replacement requests.

Slides' replace-all operation matches substrings, everywhere on the page. A
field called ``Phone`` can therefore alter ``Phone Number`` while Google still
returns success. This module proves that every literal occurs only as a complete
text element before creating any request; repeated standalone fields remain
valid.

That proof covers the design as it arrives. It does not cover what Gable itself
writes onto the design, and that gap put a wrong word on a real flyer: Bobby
Carr's brokerage name ends in "Realtor", Under Contract's title placeholder *is*
"Realtor", and the title replacement ran after the name was written — so the
flyer read "Bobby Carr The Dog Walking REALTOR". Every literal was standalone in
the source. The collision only existed once the name was on the slide.

So the fills are done in two passes. Every literal is first replaced with a
unique sentinel, and the sentinels are then replaced with the values. No pass
ever searches for a word, which means no value can be caught by a later
replacement no matter what it contains, and no ordering of the fields can
matter.

The sentinel is plain ASCII on purpose — see `SENTINEL_MARK`. A design or a
value already carrying it refuses the whole fill, and if a sentinel ever did
survive a pass, the occurrence check below reports it and the run stops rather
than delivering a flyer with a token on it.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from gable.slides.edits import replace_text
from gable.slides.elements import descendants, text_content

logger = logging.getLogger("gable.slides.replacement")

#: Opens each sentinel. Deliberately plain ASCII: **Slides silently strips
#: U+E000**, the private-use codepoint this first used. `replaceAllText` reported
#: success, the document stored `G0000` without the marks, and the second pass
#: then matched nothing — caught by the occurrence check rather than delivered,
#: but only because that check exists. Verified on a real render 2026-08-14.
#:
#: A design or a value carrying this string is refused rather than filled, so
#: the only cost of an ordinary-looking mark is a refusal that cannot happen in
#: practice: no Corner House design and no listing field contains it.
SENTINEL_MARK: Final[str] = "{{GABLE-"


def _sentinel(index: int) -> str:
    """The unique stand-in for the literal at `index`.

    Fixed width and zero-padded so no sentinel is a substring of another, which
    would reintroduce exactly the collision this exists to prevent.
    """
    return f"{SENTINEL_MARK}{index:04d}}}}}"


def safe_replacement_requests(
    presentation: dict[str, Any],
    pairs: dict[str, str],
) -> list[dict[str, Any]]:
    """Return replacements only when every literal is a standalone field.

    Args:
        presentation: The presentation to be filled, as read from the API.
        pairs: Template literal to the value replacing it.

    Returns:
        Two requests per pair — literal to sentinel, then sentinel to value — in
        that order, to be sent as one atomic batch. An empty list means the
        replacement was refused and the caller must not build.

    Raises:
        Nothing. Every refusal is logged and returned as an empty list, because
        a partly-filled flyer is worse than one that was never made.
    """
    page_ids = [str(page.get("objectId") or "") for page in presentation.get("slides", [])]
    texts = [
        text_content(element)
        for page in presentation.get("slides", [])
        for element in descendants(page.get("pageElements", []))
    ]
    for literal in pairs:
        total = sum(text.count(literal) for text in texts)
        standalone = sum(1 for text in texts if text.strip() == literal.strip())
        if total == 0:
            logger.error("refused a replacement for a literal that is not on the slide")
            return []
        if total != standalone:
            logger.error(
                "refused an unsafe text replacement: %d occurrence(s), %d standalone",
                total,
                standalone,
            )
            return []

    if any(SENTINEL_MARK in text for text in texts):
        logger.error("refused a replacement: the design already contains the sentinel mark")
        return []
    if any(SENTINEL_MARK in value for value in pairs.values()):
        logger.error("refused a replacement: a value contains the sentinel mark")
        return []

    sentinels = [_sentinel(index) for index in range(len(pairs))]
    requests: list[dict[str, Any]] = []
    for sentinel, literal in zip(sentinels, pairs, strict=True):
        requests.extend(replace_text(literal, sentinel, page_ids, allow_short=True))
    for sentinel, value in zip(sentinels, pairs.values(), strict=True):
        requests.extend(replace_text(sentinel, value, page_ids, allow_short=True))
    return requests


def confirmed_replacement_count(response: dict[str, Any], field_count: int) -> int:
    """Return logical fields changed, not raw occurrences across the slide.

    One literal may intentionally appear in two standalone boxes. Google then
    reports two occurrences for one request; summing those values makes a fully
    successful batch look incomplete to a caller expecting one result per field.
    Every request must have a reply and change at least one occurrence.

    Args:
        response: The `batchUpdate` reply.
        field_count: How many logical fields were filled — the number of pairs,
            not the number of requests, which is twice that.

    Returns:
        `field_count` when every request changed something, and -1 otherwise.

    Raises:
        Nothing.
    """
    replies = response.get("replies", [])
    if len(replies) != field_count * 2:
        return -1
    changed = [
        int(reply.get("replaceAllText", {}).get("occurrencesChanged", 0)) for reply in replies
    ]
    return field_count if all(count >= 1 for count in changed) else -1
