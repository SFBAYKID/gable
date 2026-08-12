"""Showing that Gable is working, while it works.

Building a flyer takes about thirty seconds: measure the template, crop and
publish the photo, copy, fill, place two images, re-measure, render. For that
half minute the thread is silent, and silence in Slack reads as nothing
happening — Carmen has no way to tell "working" from "broken" or "asleep".

Slack offers a real thread status for assistant apps, which renders the way a
person typing does. It is the right thing when the app is configured for it and
a plain no-op when it is not, so this tries it and quietly falls back to an
hourglass reaction. Both are cosmetic: **a failure here must never affect the
flyer**, so every call swallows its own errors rather than letting a decoration
break the work it decorates.

Does not handle: progress *within* the build. There are no percentages to show
and inventing them would be worse than an honest "working on it".
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, Final

logger = logging.getLogger("gable.status")

#: Shown while work is in flight, when the assistant status API is unavailable.
WAITING_REACTION: Final[str] = "hourglass_flowing_sand"


class Working:
    """A context manager that says "working" for as long as its body runs.

    Used as::

        with Working(client, channel, thread_ts, "Rendering the flyer"):
            build_the_flyer()

    The indicator is cleared on the way out whether the body succeeded or
    raised, because a spinner left running after a failure is worse than none:
    it says the work is still coming when it is not.
    """

    def __init__(
        self,
        client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
        channel: str,
        thread_ts: str,
        what: str = "Working on it",
        message_ts: str = "",
    ) -> None:
        """Prepare an indicator without showing it yet.

        Args:
            client: A Slack WebClient.
            channel: Channel id the thread lives in.
            thread_ts: The thread to show the status against.
            what: Short present-tense description, shown to the user.
            message_ts: A message to react to when the status API is
                unavailable. Defaults to the thread parent.
        """
        self._client = client
        self._channel = channel
        self._thread = thread_ts
        self._what = what
        self._message = message_ts or thread_ts
        self._reacted = False

    def _set_status(self, text: str) -> bool:
        """Try Slack's assistant thread status. True when it was accepted."""
        try:
            self._client.assistant_threads_setStatus(
                channel_id=self._channel, thread_ts=self._thread, status=text
            )
        except Exception:
            return False
        return True

    def _react(self, add: bool) -> None:
        """Add or remove the waiting reaction, ignoring every failure.

        A reaction that is already present, or already gone, raises from Slack.
        Neither is worth surfacing: the reaction is decoration, and the flyer is
        the work.
        """
        try:
            call = self._client.reactions_add if add else self._client.reactions_remove
            call(channel=self._channel, timestamp=self._message, name=WAITING_REACTION)
        except Exception:
            logger.debug("could not %s the waiting reaction", "add" if add else "remove")

    def start(self) -> None:
        """Show the indicator."""
        if self._set_status(f"{self._what}…"):
            return
        self._react(add=True)
        self._reacted = True

    def stop(self) -> None:
        """Clear the indicator, whatever happened."""
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
