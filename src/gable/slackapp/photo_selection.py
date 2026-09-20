"""Continue a retained photo batch after an explicit numbered main-photo choice.

Reached only from the conversational tool call in `brain.py`. The number is the
person's; nothing here reads a picture's contents to decide which is the main
one, because Gable cannot see these uploads.
"""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection
from typing import Any, Final

from gable.db import store
from gable.photos.batch import pending_uploads
from gable.slackapp.photos import PhotoHandoff

NOTHING_RETAINED: Final = (
    "I am not holding any photo uploads for this listing. Send the photos here "
    "together and tell me which one should be the main photo."
)


def select_photos(
    connection: Connection,
    handoff: PhotoHandoff,
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    thread_ts: str,
    channel: str,
    arguments: dict[str, Any],
    progress: Callable[[str], None],
) -> str:
    """Validate the number against this run's retained uploads and resume it.

    Args:
        connection: A thread-owned database connection.
        handoff: The ordinary photo handoff, which does the whole placement.
        client: The authenticated Slack web client.
        thread_ts: The listing thread the choice was made in.
        channel: The channel the handoff is allowed to work in.
        arguments: The tool call's arguments; `main_index` is one-based.
        progress: Updates the native waiting state with the actual stage.

    Returns:
        A house-style-safe sentence, or "" when the handoff already spoke.

    Raises:
        Nothing. Every failure becomes a sentence the handoff or this returns.
    """
    run = store.run_for_thread(connection, thread_ts)
    if run is None or not run.is_paused:
        return NOTHING_RETAINED
    files = pending_uploads(run.pending_photo_files)
    if not files:
        return NOTHING_RETAINED
    selected = arguments.get("main_index")
    if (
        not isinstance(selected, int)
        or isinstance(selected, bool)
        or not 1 <= selected <= len(files)
    ):
        ordinals = "first or second" if len(files) == 2 else "first, second or third"
        return f"Which of the {len(files)} photos should be the main one: {ordinals}?"
    # No mimetype is invented here. The handoff reads each file's real type
    # back from Slack before it downloads anything, which is the only place
    # that knows it.
    return handoff.handle(
        {
            "channel": channel,
            "thread_ts": thread_ts,
            "text": "",
            "files": [{"id": item} for item in files],
            "property_main_index": selected - 1,
        },
        client,
        progress,
    )
