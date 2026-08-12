"""Showing that Gable is thinking, in the thread, while it thinks.

A reply takes four to eight seconds and a flyer about thirty. For that time the
thread is silent, and silence in Slack reads as nothing happening — no way to
tell "working" from "broken" from "asleep".

Slack's own `assistant.threads.setStatus` looked like the answer and is not.
Called against a normal channel thread it returns `ok` and renders nothing:
verified on 2026-08-12 by holding a status open for twenty seconds on a live
thread with nothing visible throughout. That status is drawn inside an assistant
pane, and Gable is talked to in an ordinary channel thread.

So the indicator is a real message. It is posted into the thread, its text is
cycled so it visibly animates, and it is **deleted** when the answer arrives —
which is the behaviour asked for: it comes in, it runs, it goes away. An earlier
attempt edited the placeholder into the answer instead, so it never went away,
it turned into the reply.

Everything here is cosmetic, so **a failure must never affect the reply**. Every
call swallows its own error, the indicator runs on a background thread so
showing it cannot delay the work it covers, and deletion happens in a `finally`
so a crash cannot strand it in the thread.

Does not handle: progress *within* the work. There is no percentage to report and
inventing one would be worse than an honest "working on it".
"""

from __future__ import annotations

import logging
import threading
from types import TracebackType
from typing import Any, Final

logger = logging.getLogger("gable.status")

#: The frames of the animation, cycled in order. An ellipsis that grows reads as
#: motion without needing a custom animated emoji, which most workspaces do not
#: have and which cannot be relied on.
FRAMES: Final[tuple[str, ...]] = (
    "_Thinking_ :hourglass_flowing_sand:",
    "_Thinking_ .",
    "_Thinking_ . .",
    "_Thinking_ . . .",
)

#: Seconds between frames. `chat.update` is rate limited around fifty calls a
#: minute, so a one-second cycle would sit on the ceiling during a long build.
FRAME_SECONDS: Final[float] = 1.5

#: How long `stop` waits for the indicator to have been posted before deleting
#: it. Without this a fast reply can delete before the post lands, leaving the
#: indicator behind for good.
_POST_TIMEOUT_SECONDS: Final[float] = 4.0


class Working:
    """Shows an animated indicator in a thread for as long as its body runs.

    Used as::

        with Working(client, channel, thread_ts):
            answer = think_about_it()

    Removed on the way out whether the body returned or raised. An indicator
    left running after a failure is worse than none: it promises an answer that
    is never coming.
    """

    def __init__(
        self,
        client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
        channel: str,
        thread_ts: str,
        what: str = "",
        message_ts: str = "",
    ) -> None:
        """Prepare an indicator without showing it yet.

        Args:
            client: A Slack WebClient.
            channel: Channel id the thread lives in.
            thread_ts: The thread to post into. Empty disables the indicator
                entirely, since a loose message in the channel would be worse
                than none.
            what: Unused; kept so existing call sites need not change. The
                frames say "Thinking" and a build says the same thing.
            message_ts: Unused; retained for the same reason.
        """
        del what, message_ts
        self._client = client
        self._channel = channel
        self._thread = thread_ts
        self._ts = ""
        self._posted = threading.Event()
        self._done = threading.Event()

    def _run(self) -> None:
        """Post the indicator, then animate it until asked to stop."""
        try:
            posted = self._client.chat_postMessage(
                channel=self._channel, thread_ts=self._thread, text=FRAMES[0]
            )
            self._ts = str(posted.get("ts") or "")
        except Exception:
            logger.debug("could not post the thinking indicator")
        finally:
            # Set even on failure, or `stop` waits the full timeout for a
            # message that was never posted and delays every reply.
            self._posted.set()

        if not self._ts:
            return
        frame = 1
        while not self._done.wait(timeout=FRAME_SECONDS):
            try:
                self._client.chat_update(
                    channel=self._channel, ts=self._ts, text=FRAMES[frame % len(FRAMES)]
                )
            except Exception:
                logger.debug("could not advance the thinking indicator")
            frame += 1

    def start(self) -> None:
        """Show the indicator without making the caller wait for it.

        A Slack call costs a few hundred milliseconds. Posting inline would delay
        the work the indicator exists to cover, so the indicator would appear
        late *because* it was being shown.
        """
        if not self._channel or not self._thread:
            self._posted.set()
            return
        threading.Thread(target=self._run, daemon=True, name="gable-status").start()

    def stop(self) -> None:
        """Stop animating and remove the indicator entirely."""
        self._done.set()
        self._posted.wait(timeout=_POST_TIMEOUT_SECONDS)
        if not self._ts:
            return
        try:
            self._client.chat_delete(channel=self._channel, ts=self._ts)
        except Exception:
            logger.debug("could not remove the thinking indicator")
        self._ts = ""

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
        """Remove the indicator on the way out, success or failure."""
        self.stop()
