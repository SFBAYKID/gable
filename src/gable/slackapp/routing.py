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
DEFAULT_REPLAY_CACHE_SIZE: Final[int] = 2048

# Human-authored thread broadcasts are still ordinary replies. All other
# subtypes describe edits, deletions, joins, or app-authored messages and must
# stay outside the conversational path.
_ROUTABLE_SUBTYPES: Final[frozenset[str]] = frozenset(("", "file_share", "thread_broadcast"))


class MessageRoute(Enum):
    """The only ordinary-message paths Gable is allowed to take."""

    IGNORE = "ignore"
    FILE_SHARE = "file_share"
    THREAD_REPLY = "thread_reply"


def has_shared_files(event: dict[str, Any]) -> bool:
    """Return whether Slack says this message carries one or more files.

    Current Events API file messages can arrive as ordinary ``message`` events
    with a ``files`` array and no ``file_share`` subtype. App-mention events may
    carry the same array. Routing only on the legacy subtype silently turns a
    property-photo upload into a conversation prompt.
    """
    files = event.get("files")
    return isinstance(files, list) and bool(files)


class EventReplayGuard:
    """Suppress repeated delivery of one already-accepted Slack event.

    Slack retries an event when its acknowledgement is lost. Bolt acknowledges
    before these handlers run, but a dropped acknowledgement can still deliver
    the same file or edit again. The event timestamp or client message id is a
    stable message identity; a bounded in-process cache prevents duplicate work
    without treating an unverifiable event as a replay.
    """

    def __init__(self, max_entries: int = DEFAULT_REPLAY_CACHE_SIZE) -> None:
        """Create a bounded, thread-safe replay cache."""
        if max_entries < 1:
            msg = "Slack replay cache size must be positive"
            raise ValueError(msg)
        self._max_entries = max_entries
        self._seen: OrderedDict[tuple[str, str, str, str], None] = OrderedDict()
        self._lock = threading.Lock()

    def first_delivery(self, event: dict[str, Any], *, route: str) -> bool:
        """Return true exactly once for an identifiable event and route.

        ``route`` lets independent event families reuse an identifier safely.
        Gable passes the same route for accepted app mentions and ordinary
        messages because Slack can describe one source message through both
        subscriptions; ignored ordinary messages never reach this guard.
        """
        identity = str(event.get("client_msg_id") or event.get("event_ts") or event.get("ts") or "")
        if not identity:
            return True
        key = (
            route,
            str(event.get("channel") or ""),
            str(event.get("user") or ""),
            identity,
        )
        with self._lock:
            if key in self._seen:
                self._seen.move_to_end(key)
                return False
            self._seen[key] = None
            while len(self._seen) > self._max_entries:
                self._seen.popitem(last=False)
        return True


class ThreadOwnership:
    """Resolve and cache which Slack thread roots belong to Gable."""

    def __init__(
        self,
        max_entries: int = DEFAULT_CACHE_SIZE,
        *,
        allowed_user_ids: frozenset[str] = frozenset(),
    ) -> None:
        """Create a bounded, process-local ownership cache.

        Args:
            max_entries: Maximum resolved channel/thread pairs retained.
            allowed_user_ids: Stable IDs allowed to create a human-authored
                Gable conversation. Empty is reserved for isolated tests; the
                production runtime always supplies Carmen and Chase.

        Raises:
            ValueError: If ``max_entries`` is not positive.
        """
        if max_entries < 1:
            msg = "thread ownership cache size must be positive"
            raise ValueError(msg)
        self._max_entries = max_entries
        self._allowed_user_ids = allowed_user_ids
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
        if subtype not in _ROUTABLE_SUBTYPES:
            return MessageRoute.IGNORE
        if not event.get("thread_ts"):
            return MessageRoute.IGNORE
        if not self._belongs_to_gable(event, client, bot_user_id=bot_user_id, bot_id=bot_id):
            return MessageRoute.IGNORE
        if subtype == "file_share" or has_shared_files(event):
            return MessageRoute.FILE_SHARE
        return MessageRoute.THREAD_REPLY

    def remember_owned(self, channel: str, thread_ts: str) -> None:
        """Record a root mention that this process just received from Slack.

        A top-level ``app_mention`` is authoritative ownership evidence. Remembering
        it immediately avoids an eventual-consistency race where the first reply
        arrives before ``conversations.replies`` can return the new root.
        """
        if channel and thread_ts:
            self._remember((channel, thread_ts), True)

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
                # An empty reply is not proof of foreign ownership. A brand-new
                # mention root can be briefly absent from the read API; failing
                # closed for this event is correct, caching the uncertainty is not.
                return False
            root = messages[0]
            if not isinstance(root, dict):
                return False
            owned = self._root_belongs_to_gable(
                root,
                bot_user_id=bot_user_id,
                bot_id=bot_id,
                allowed_user_ids=self._allowed_user_ids,
            )
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
        allowed_user_ids: frozenset[str],
    ) -> bool:
        """Recognize a Gable root or an allowed human root that called Gable."""
        root_user_id = str(root.get("user") or "")
        root_bot_id = str(root.get("bot_id") or "")
        if bot_id and root_bot_id == bot_id:
            return True
        if (
            bot_user_id
            and root_user_id == bot_user_id
            and (not root_bot_id or root_bot_id == bot_id)
        ):
            return True
        if not bot_user_id:
            return False
        # Another app may explicitly mention Gable in its own root. That mention
        # authorizes only the one direct response handled by ``app_mention``; it
        # must never transfer the thread to Gable for later unmentioned replies.
        if root_bot_id or root.get("app_id") or root.get("subtype") == "bot_message":
            return False
        if not root_user_id or (allowed_user_ids and root_user_id not in allowed_user_ids):
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
