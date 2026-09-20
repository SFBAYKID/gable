"""Read the bounded, durable property-photo record a run carries.

The record is written only by Gable, as `json.dumps` of `{"id", "url"}` rows in
placement order: the main photograph first, then the smaller spaces left to
right. Nothing else writes it, so malformed state means a defect rather than a
person's input — and a defect must not take a build down with it.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

logger = logging.getLogger("gable.photos.batch")

#: The most property photographs any measured design can hold: one main well
#: plus the two-well row under it. Measured on all six live designs 2026-09-20
#: and recorded in `slides/property_frames.py`; nothing reads a larger batch.
MAX_PHOTOS: Final[int] = 3


def stored_photos(value: str) -> list[dict[str, str]]:
    """Read the persisted photo records, degrading to none on malformed state.

    Args:
        value: The `runs.property_photos` column, ordinarily `"[]"`.

    Returns:
        The photo records in placement order, or `[]` when the column does not
        hold a bounded list of `{"id", "url"}` string pairs. Empty is the safe
        answer rather than an exception: every caller falls back to the run's
        single `photo_url`, which is the behaviour that predates the batch, so
        a corrupt column costs the smaller photographs and not the flyer.

    Raises:
        Nothing.
    """
    try:
        rows: Any = json.loads(value or "[]")
    except ValueError:
        logger.warning("a run's property photo record was not readable JSON")
        return []
    if not isinstance(rows, list) or len(rows) > MAX_PHOTOS:
        logger.warning("a run's property photo record was not a bounded list")
        return []
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("id"), str)
        or not isinstance(row.get("url"), str)
        or not row["id"]
        or not row["url"]
        for row in rows
    ):
        logger.warning("a run's property photo record held an unusable entry")
        return []
    return [{"id": row["id"], "url": row["url"]} for row in rows]


def pending_uploads(value: str) -> list[str]:
    """Read the retained upload ids a run is holding for a main-photo choice.

    Args:
        value: The `runs.pending_photo_files` column, ordinarily `"[]"`.

    Returns:
        The Slack file ids in upload order, or `[]` when the column does not
        hold a bounded list of distinct non-empty strings.

    Raises:
        Nothing.
    """
    try:
        rows: Any = json.loads(value or "[]")
    except ValueError:
        logger.warning("a run's retained upload record was not readable JSON")
        return []
    if not isinstance(rows, list) or len(rows) > MAX_PHOTOS:
        logger.warning("a run's retained upload record was not a bounded list")
        return []
    if any(not isinstance(row, str) or not row for row in rows) or len(set(rows)) != len(rows):
        logger.warning("a run's retained upload record held an unusable entry")
        return []
    return [str(row) for row in rows]
