"""Bounded property-photo batches and explicit upload-order selection."""

from __future__ import annotations

import hashlib
import json
import re

from gable.photos.batch import MAX_PHOTOS as MAX_PHOTOS


def main_index(text: str, count: int) -> int | None:
    """Read an explicit ordinal main-photo instruction, refusing conflicts.

    No picture is picked from its visual content. A one-picture upload is
    already unambiguous. More elaborate instructions go through the normal
    conversational selection tool against the retained numbered uploads.
    """
    if count == 1:
        return 0
    words = {
        "first": 0,
        "1st": 0,
        "1": 0,
        "second": 1,
        "2nd": 1,
        "2": 1,
        "third": 2,
        "3rd": 2,
        "3": 2,
    }
    lower = text.casefold()
    if not re.search(r"\b(main|large|largest|big|hero)\b", lower):
        return None
    if re.search(r"\b(not|don't|except|instead)\b", lower):
        return None
    found = {
        words[token] for token in re.findall(r"\b(first|1st|1|second|2nd|2|third|3rd|3)\b", lower)
    }
    if len(found) == 1 and (index := next(iter(found))) < count:
        return index
    return None


def batch_id(file_ids: list[str], index: int | None) -> str:
    """Stable identity shared by message and file_shared event routes."""
    if len(file_ids) == 1:
        return file_ids[0]
    digest = hashlib.sha256(json.dumps(file_ids).encode()).hexdigest()[:32]
    return f"photos:{digest}:{index if index is not None else 'choose'}"
