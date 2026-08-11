"""The house style, re-exported for Slack code.

The rules live in `gable.voice`, because they describe how Gable speaks rather
than how Slack works, and CLAUDE.md §6 forbids anything under `src/gable/` from
importing `slackapp/`. Moving them let the pipeline apply the same rules without
breaking that layering. This module keeps `from gable.slackapp.style import ...`
working for the Slack code that already used it.
"""

from __future__ import annotations

from gable.voice import (
    humanize_error,
    is_clean,
    link,
    missing_fields_sentence,
    quote_rail,
    safe,
    strip_to_plain,
    violations,
)

__all__ = [
    "humanize_error",
    "is_clean",
    "link",
    "missing_fields_sentence",
    "quote_rail",
    "safe",
    "strip_to_plain",
    "violations",
]
