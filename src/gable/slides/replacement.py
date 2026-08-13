"""Build substring-safe Google Slides text replacement requests.

Slides' replace-all operation matches substrings.  A field called ``Phone``
can therefore alter ``Phone Number`` while Google still returns success.  This
module proves that every literal occurs only as a complete text element before
creating any request; repeated standalone fields remain valid.
"""

from __future__ import annotations

import logging
from typing import Any

from gable.slides.edits import replace_text
from gable.slides.elements import descendants, text_content

logger = logging.getLogger("gable.slides.replacement")


def safe_replacement_requests(
    presentation: dict[str, Any],
    pairs: dict[str, str],
) -> list[dict[str, Any]]:
    """Return replacements only when every literal is a standalone field."""
    page_ids = [str(page.get("objectId") or "") for page in presentation.get("slides", [])]
    texts = [
        text_content(element)
        for page in presentation.get("slides", [])
        for element in descendants(page.get("pageElements", []))
    ]
    requests: list[dict[str, Any]] = []
    for literal, value in pairs.items():
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
        requests.extend(replace_text(literal, value, page_ids, allow_short=True))
    return requests


def confirmed_replacement_count(response: dict[str, Any], request_count: int) -> int:
    """Return logical fields changed, not raw occurrences across the slide.

    One literal may intentionally appear in two standalone boxes. Google then
    reports two occurrences for one request; summing those values makes a fully
    successful batch look incomplete to a caller expecting one result per
    field. Every request must have a reply and change at least one occurrence.
    """
    replies = response.get("replies", [])
    if len(replies) != request_count:
        return -1
    changed = [
        int(reply.get("replaceAllText", {}).get("occurrencesChanged", 0)) for reply in replies
    ]
    return request_count if all(count >= 1 for count in changed) else -1
