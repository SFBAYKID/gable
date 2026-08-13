"""Decide whether an ordinary Slack thread reply belongs to Gable.

Direct app mentions are routed by Slack's ``app_mention`` event and do not pass
through this module. Ordinary replies and file shares are accepted only when
the thread root was written by Gable or originally mentioned Gable. A lookup
failure stays silent: replying in another agent's thread is worse than requiring
one explicit mention after a temporary Slack failure.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from enum import Enum
from typing import Any, Final

logger = logging.getLogger("gable.slack.routing")

DEFAULT_CACHE_SIZE: Final[int] = 512


class MessageRoute(Enum):
    """The only ordinary-message paths Gable is allowed to take."""

    IGNORE = "ignore"
    FILE_SHARE = "file_share"
    THREAD_REPLY = "thread_reply"


class ThreadOwnership:
    """Resolve and cache which Slack thread roots belong to Gable."""

    def __init__(self, max_entries: int = DEFAULT_CACHE_SIZE) -> None:
        """Create a bounded, process-local ownership cache.

        Args:
            max_entries: Maximum resolved channel/thread pairs retained.

        Raises:
            ValueError: If ``max_entries`` is not positive.
        """
        if max_entries < 1:
            msg = "thread ownership cache size must be positive"
            raise ValueError(msg)
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[str, str], bool] = OrderedDict()
        self._lock = threading.Lock()

    def route(
        self,
        event: dict[str, Any],
        client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
        *,
        bot_user_id: str,
        bot_id: str,
    ) -> MessageRoute:
        """Choose a route for one ordinary Slack message event.

        Args:
            event: Slack's message event.
            client: Slack Web API client used only when the root must be read.
            bot_user_id: Gable's Slack bot-user id from Bolt context.
            bot_id: Gable's Slack bot id from Bolt context.

        Returns:
            ``FILE_SHARE`` or ``THREAD_REPLY`` only for a Gable-owned thread;
            ``IGNORE`` for every top-level, bot-authored, foreign, malformed, or
            unverifiable event.

        Raises:
            Nothing. Slack read failures are logged and fail closed.
        """
        if event.get("bot_id"):
            return MessageRoute.IGNORE
        subtype = str(event.get("subtype") or "")
        if subtype and subtype != "file_share":
            return MessageRoute.IGNORE
        if not event.get("thread_ts"):
            return MessageRoute.IGNORE
        if not self._belongs_to_gable(event, client, bot_user_id=bot_user_id, bot_id=bot_id):
            return MessageRoute.IGNORE
        if subtype == "file_share":
            return MessageRoute.FILE_SHARE
        return MessageRoute.THREAD_REPLY

    def _belongs_to_gable(
        self,
        event: dict[str, Any],
        client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
        *,
        bot_user_id: str,
        bot_id: str,
    ) -> bool:
        """Return whether the event's root was written by or addressed to Gable."""
        channel = str(event.get("channel") or "")
        thread_ts = str(event.get("thread_ts") or "")
        if not channel or not thread_ts or not (bot_user_id or bot_id):
            return False
        if bot_user_id and event.get("parent_user_id") == bot_user_id:
            self._remember((channel, thread_ts), True)
            return True

        key = (channel, thread_ts)
        cached = self._cached(key)
        if cached is not None:
            return cached

        try:
            # Slack returns the root as the first message in a thread.
            # https://docs.slack.dev/reference/methods/conversations.replies/
            response = client.conversations_replies(channel=channel, ts=thread_ts, limit=1)
            messages = response.get("messages", [])
            if not isinstance(messages, list) or not messages:
                self._remember(key, False)
                return False
            root = messages[0]
            if not isinstance(root, dict):
                self._remember(key, False)
                return False
            owned = self._root_belongs_to_gable(root, bot_user_id=bot_user_id, bot_id=bot_id)
            self._remember(key, owned)
            return owned
        except Exception:
            logger.exception("could not establish Slack thread ownership; staying silent")
            return False

    @staticmethod
    def _root_belongs_to_gable(
        root: dict[str, Any],
        *,
        bot_user_id: str,
        bot_id: str,
    ) -> bool:
        """Recognize a Gable-authored root or a human root that called Gable."""
        if bot_user_id and str(root.get("user") or "") == bot_user_id:
            return True
        if bot_id and str(root.get("bot_id") or "") == bot_id:
            return True
        if not bot_user_id:
            return False
        mention = re.compile(rf"<@{re.escape(bot_user_id)}(?:\|[^>]*)?>")
        return mention.search(str(root.get("text") or "")) is not None

    def _cached(self, key: tuple[str, str]) -> bool | None:
        """Read one cached decision and mark it most recently used."""
        with self._lock:
            if key not in self._cache:
                return None
            value = self._cache.pop(key)
            self._cache[key] = value
            return value

    def _remember(self, key: tuple[str, str], value: bool) -> None:
        """Store one decision while keeping memory strictly bounded."""
        with self._lock:
            self._cache.pop(key, None)
            self._cache[key] = value
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
