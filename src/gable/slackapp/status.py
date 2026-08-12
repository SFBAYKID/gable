"""Showing that Gable is thinking, in the thread, while it thinks.

Building a reply takes several seconds and a flyer takes about thirty. For that
time the thread is silent, and silence in Slack reads as nothing happening —
there is no way to tell "working" from "broken" from "asleep".

Slack has a real indicator for this: `assistant.threads.setStatus` puts an
animated line in the thread that clears when the reply lands, which is exactly
the shape wanted. It takes `chat:write`, which this app already has. An earlier
version of this module assumed it needed `assistant:write`, so it fell back to
an emoji reaction nobody noticed — verified against the live API on 2026-08-12,
which returned `ok` on the first call.

Everything here is cosmetic, so **a failure must never affect the reply**. Each
call swallows its own error, and the indicator goes up on a background thread so
showing it cannot itself delay the work it exists to cover.

Does not handle: progress *within* the work. There is no percentage to report and
inventing one would be worse than an honest "working on it".
"""

from __future__ import annotations

import logging
import threading
from types import TracebackType
from typing import Any, Final

logger = logging.getLogger("gable.status")

#: Shown when the thread status is unavailable. A reaction is far quieter than
#: the real indicator, so it is a fallback rather than a choice.
WAITING_REACTION: Final[str] = "hourglass_flowing_sand"

#: How long `stop` waits for the indicator to have gone up before clearing it.
#: Clearing before showing leaves it stuck on afterwards, which says the reply is
#: still coming when it has already arrived.
_SHOW_TIMEOUT_SECONDS: Final[float] = 3.0


class Working:
    """Shows a thinking indicator for as long as its body runs.

    Used as::

        with Working(client, channel, thread_ts, "is reading the template"):
            build_the_flyer()

    Cleared on the way out whether the body returned or raised. An indicator
    left spinning after a failure is worse than none: it promises an answer that
    is never coming.
    """

    def __init__(
        self,
        client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
        channel: str,
        thread_ts: str,
        what: str = "is thinking",
        message_ts: str = "",
    ) -> None:
        """Prepare an indicator without showing it yet.

        Args:
            client: A Slack WebClient.
            channel: Channel id the thread lives in.
            thread_ts: Thread to show the indicator in. Slack has no notion of a
                status outside a thread, so an empty value disables it.
            what: Present-tense description, shown to the reader.
            message_ts: Message to react to if the thread status is unavailable.
                Defaults to the thread parent.
        """
        self._client = client
        self._channel = channel
        self._thread = thread_ts
        self._what = what
        self._message = message_ts or thread_ts
        self._reacted = False
        self._shown = threading.Event()

    def _set_status(self, text: str) -> bool:
        """Set or clear the thread status. True when Slack accepted it.

        Failure is deliberately not remembered between instances. An earlier
        version cached it on the class, so one transient error disabled the real
        indicator for the whole process and quietly downgraded every later reply
        to an emoji nobody saw.
        """
        if not self._channel or not self._thread:
            return False
        try:
            self._client.assistant_threads_setStatus(
                channel_id=self._channel, thread_ts=self._thread, status=text
            )
        except Exception:
            logger.debug("thread status unavailable; falling back to a reaction")
            return False
        return True

    def _react(self, add: bool) -> None:
        """Add or remove the waiting reaction, ignoring every failure.

        Slack raises when a reaction is already present, or already gone. Both
        happen in normal use — a retry, or two replies in one thread — and
        neither is worth surfacing.
        """
        if not self._channel or not self._message:
            return
        try:
            call = self._client.reactions_add if add else self._client.reactions_remove
            call(channel=self._channel, timestamp=self._message, name=WAITING_REACTION)
        except Exception:
            logger.debug("could not %s the waiting reaction", "add" if add else "remove")

    def _show(self) -> None:
        """Put the indicator up, by whichever route works."""
        try:
            if not self._set_status(self._what):
                self._react(add=True)
                self._reacted = True
        finally:
            # Set even on failure: `stop` waits on this, and a wait that never
            # completes would add seconds to every reply.
            self._shown.set()

    def start(self) -> None:
        """Show the indicator without making the caller wait for it.

        A Slack call costs a few hundred milliseconds. Doing it inline delays the
        work the indicator exists to cover, so the indicator would appear late
        *because* it was being shown.
        """
        threading.Thread(target=self._show, daemon=True, name="gable-status").start()

    def stop(self) -> None:
        """Clear the indicator, whatever happened."""
        self._shown.wait(timeout=_SHOW_TIMEOUT_SECONDS)
        self._set_status("")
        if self._reacted:
            self._react(add=False)
            self._reacted = False

    def __enter__(self) -> Working:
        """Show the indicator and return self."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Clear the indicator on the way out, success or failure."""
        self.stop()
