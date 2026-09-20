"""Recover a complete ordered upload message from Slack file notifications."""

from __future__ import annotations

import logging
import time
from typing import Any, Final

logger = logging.getLogger("gable.slack.photos")

#: A bounded read budget. Two tries with a short pause covers the ordinary
#: transient, and a longer wait is worse than one photograph: Slack redelivers
#: the event, and the person is watching a thread that has said nothing yet.
_HISTORY_ATTEMPTS: Final[int] = 2
_HISTORY_BACKOFF_SECONDS: Final[float] = 0.5

#: How long to let a part-attached upload finish before reading it again.
_SETTLE_SECONDS: Final[float] = 0.6


def shared_file_event(event: dict[str, Any], client: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Turn a ``file_shared`` notice into the message-shaped event the handoff reads.

    Slack's current upload flow can post the message first and attach the file a
    moment later. The ``message`` event then arrives with no ``files`` array at
    all, and the upload is announced only by ``file_shared`` — which Gable did
    not subscribe to until 2026-08-19. That is why Caleb Olawuyi's photo never
    reached it while Carmen's next one did: a race, not a broken path.

    The result is deliberately shaped like the message event, so exactly one
    code path fits and places a photograph however Slack chose to announce it.

    Args:
        event: Slack's ``file_shared`` event.
        client: Slack Web API client.

    Returns:
        A message-shaped event carrying the file, its channel and its thread, or
        ``None`` when the file cannot be placed in exactly one thread. ``None``
        is the safe answer: the ordinary message path may still carry it, and
        guessing a thread would put somebody's photo on another listing.

    Raises:
        Nothing. A lookup failure is logged and becomes ``None``.
    """
    file_id = str(event.get("file_id") or (event.get("file") or {}).get("id") or "").strip()
    if not file_id:
        return None
    try:
        # https://docs.slack.dev/reference/methods/files.info/
        answer = client.files_info(file=file_id)
    except Exception:
        logger.exception("could not read the details of shared file %s", file_id)
        return None
    info = _field(answer, "file")
    if not isinstance(info, dict):
        return None
    if not str(info.get("mimetype") or "").startswith("image/"):
        return None

    shares = info.get("shares")
    placements: list[tuple[str, dict[str, Any]]] = []
    if isinstance(shares, dict):
        for group in shares.values():
            if not isinstance(group, dict):
                continue
            for channel_id, entries in group.items():
                if not isinstance(entries, list):
                    continue
                placements.extend(
                    (str(channel_id), entry) for entry in entries if isinstance(entry, dict)
                )
    # One share is the ordinary case. Several means the same file sits in more
    # than one place, and choosing between them is the guess this refuses.
    if len(placements) != 1:
        return None
    channel_id, placement = placements[0]
    thread_ts = str(placement.get("thread_ts") or placement.get("ts") or "")
    if not thread_ts:
        return None
    message_ts = str(placement.get("ts") or "")
    alone: list[dict[str, Any]] = [{"id": file_id, "mimetype": str(info.get("mimetype") or "")}]
    message = _parent_message(client, channel_id, thread_ts, message_ts)
    if message is not None and len(_images(message)) < 2:
        # This route exists because Slack can attach a file to a message it has
        # already posted, which is how one photo was lost on 2026-08-19. The
        # same lag applies to the second and third file of a batch, and the
        # replay guard keys on the message, so whichever sibling arrives first
        # decides what Gable sees -- silently. One settle read closes that: a
        # batch still mid-attachment reads as complete a moment later, and a
        # genuine single upload pays one call it can afford. Skipped entirely
        # when the history could not be read at all: that budget is spent.
        time.sleep(_SETTLE_SECONDS)
        settled = _parent_message(client, channel_id, thread_ts, message_ts)
        if len(_images(settled)) > len(_images(message)):
            message = settled
    # The whole message, so a three-photo upload is one batch rather than three
    # separate listings' worth of single photos racing each other. When the
    # history cannot be read, this falls back to the file it was told about --
    # which is exactly what this route did before batches existed, so a
    # transient failure costs the smaller photographs and never the upload.
    siblings = _images(message)
    if file_id not in {str(item.get("id") or "") for item in siblings}:
        if siblings:
            logger.warning("shared file %s was not in its own upload message", file_id)
        siblings = alone
    return {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "ts": message_ts,
        "user": str(info.get("user") or event.get("user_id") or ""),
        "parent_user_id": str(placement.get("parent_user_id") or ""),
        "text": str((message or {}).get("text") or ""),
        "files": siblings,
    }


def _parent_message(
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    channel_id: str,
    thread_ts: str,
    message_ts: str,
) -> dict[str, Any] | None:
    """Read the message that carried a shared file, for its words and siblings.

    The caption is load-bearing twice over: values stated beside a photo are
    recorded from it, and a delivered flyer only accepts a replacement when the
    words ask for one. Shaping this route as an empty-text event threw it away,
    so "here is a better angle, run it again" worked when Slack announced the
    upload as a message and silently did not when it announced a file share.

    Args:
        client: Slack Web API client.
        channel_id: The channel the file was shared in.
        thread_ts: The thread root.
        message_ts: The message that carried the file.

    Returns:
        The message, or None when it cannot be read.

    Raises:
        Nothing. A lookup failure is logged and becomes None.
    """
    if not message_ts:
        return None
    for attempt in range(_HISTORY_ATTEMPTS):
        try:
            # https://docs.slack.dev/reference/methods/conversations.replies/
            answer = client.conversations_replies(
                channel=channel_id, ts=thread_ts, latest=message_ts, inclusive=True, limit=20
            )
        except Exception:
            logger.exception("could not recover the photo upload message (attempt %d)", attempt + 1)
            if attempt + 1 < _HISTORY_ATTEMPTS:
                time.sleep(_HISTORY_BACKOFF_SECONDS * (attempt + 1))
            continue
        messages = _field(answer, "messages")
        for item in messages if isinstance(messages, list) else []:
            if isinstance(item, dict) and str(item.get("ts") or "") == message_ts:
                return item
        return None
    return None


def _images(message: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the image attachments of a message, in the order Slack lists it.

    Args:
        message: A Slack message, or None when it could not be read.

    Returns:
        The image files, or `[]`. Upload order is placement order, so the list
        is never reordered here.

    Raises:
        Nothing.
    """
    return [
        item
        for item in (message or {}).get("files") or []
        if isinstance(item, dict) and str(item.get("mimetype") or "").startswith("image/")
    ]


def _field(answer: Any, name: str) -> Any:  # noqa: ANN401 - Slack payloads are untyped
    """Read one field from a Slack reply, whatever wrapper the SDK returned.

    `slack_sdk` returns a `SlackResponse`, which supports `.get` but is **not**
    a `dict`. Guarding these reads with `isinstance(answer, dict)` therefore
    discarded every real reply while every test passed, because the fakes hand
    back plain dicts. That made `shared_file_event` return None for every live
    upload from the day it was added — so the `file_shared` route written to
    stop a photo being lost never ran, and the loss it was written for could
    happen again with nothing in the log. Found on a real upload 2026-09-20.

    Args:
        answer: A Slack Web API reply, or anything else.
        name: The top-level field to read.

    Returns:
        The field, or None when the reply cannot be read at all.

    Raises:
        Nothing. A reply shaped unlike either form becomes None.
    """
    getter = getattr(answer, "get", None)
    if not callable(getter):
        logger.warning("a Slack reply was not readable as a mapping")
        return None
    try:
        return getter(name)
    except Exception:
        logger.exception("a Slack reply could not be read")
        return None
