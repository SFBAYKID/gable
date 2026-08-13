"""Show Slack's native purple waiting state while Gable prepares a response.

Slack owns the visual treatment: ``assistant.threads.setStatus`` renders the app
name with its pulsing purple loading treatment and clears it when a reply lands.
The method supports channel-based apps with ``chat:write`` as of Slack's March
2026 scope update, so Gable does not need an assistant pane or another scope.

The copy has two phases. A short wait gets a little personality; work lasting
more than a few seconds names the real stage supplied by the caller. Status is
cosmetic: every Slack failure is swallowed, updates run away from the work they
describe, and the final empty status is attempted even when the work raises.

Does not handle: deciding what stage a workflow is in. Callers must provide a
literal, truthful stage rather than a percentage or a guessed operation.
"""

from __future__ import annotations

import logging
import threading
from types import TracebackType
from typing import Any, Final

logger = logging.getLogger("gable.status")

# Slack automatically prefixes each value with the app name. These therefore
# read as "Gable is thinking...", not as detached narration.
INITIAL_STATUS: Final[str] = "is thinking..."

# Each pair is how long the preceding status remains visible, followed by its
# replacement. The last playful line stays for one second so truthful workflow
# detail begins once the wait crosses six seconds.
LEAD_IN_STEPS: Final[tuple[tuple[float, str], ...]] = (
    (1.0, "says hold tight..."),
    (2.0, "is jittering..."),
    (2.0, "is bobbing and weaving..."),
)
FINAL_LEAD_IN_SECONDS: Final[float] = 1.0
STAGE_REFRESH_SECONDS: Final[float] = 2.0

# A stalled cosmetic request must not hold up the reply. The worker owns the
# eventual empty-status call if it does not stop inside this small join window.
STOP_JOIN_SECONDS: Final[float] = 1.0


class Working:
    """Keep Slack's native waiting treatment active for one response.

    Used as::

        with Working(client, channel, thread_ts, "is preparing the answer...") as wait:
            decision = think_about_it()
            wait.stage("is updating the flyer...")
            apply_the_change(decision)

    The answer should be posted inside the context. Slack then clears the native
    status automatically, and ``__exit__`` sends an explicit empty status as the
    failure-safe cleanup.
    """

    def __init__(
        self,
        client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
        channel: str,
        thread_ts: str,
        stage: str = "is preparing the answer...",
    ) -> None:
        """Prepare an indicator without showing it yet.

        Args:
            client: A Slack WebClient.
            channel: Channel id containing the conversation.
            thread_ts: Root timestamp the response will be posted beneath.
                Empty disables the indicator rather than creating loose status.
            stage: Truthful present-tense work shown after the playful lead-in.

        Raises:
            Nothing.
        """
        self._client = client
        self._channel = channel
        self._thread = thread_ts
        self._stage = stage
        self._stage_lock = threading.Lock()
        self._done = threading.Event()
        self._worker: threading.Thread | None = None

    def _set_status(self, text: str) -> None:
        """Ask Slack to show one native status, ignoring every failure.

        Slack contract: https://docs.slack.dev/reference/methods/assistant.threads.setStatus/
        """
        if not self._channel or not self._thread:
            return
        try:
            self._client.assistant_threads_setStatus(
                channel_id=self._channel,
                thread_ts=self._thread,
                status=text,
            )
        except Exception:
            logger.debug("could not update the native thinking indicator")

    def _current_stage(self) -> str:
        """Return the latest workflow stage without racing its caller."""
        with self._stage_lock:
            return self._stage

    def _run(self) -> None:
        """Advance timed copy, then refresh the caller's current real stage."""
        try:
            if self._done.wait(timeout=LEAD_IN_STEPS[0][0]):
                return
            self._set_status(LEAD_IN_STEPS[0][1])

            for delay, text in LEAD_IN_STEPS[1:]:
                if self._done.wait(timeout=delay):
                    return
                self._set_status(text)

            if self._done.wait(timeout=FINAL_LEAD_IN_SECONDS):
                return
            self._set_status(self._current_stage())
            while not self._done.wait(timeout=STAGE_REFRESH_SECONDS):
                self._set_status(self._current_stage())
        finally:
            self._set_status("")

    def start(self) -> None:
        """Show the native state immediately and start timed updates.

        The first call is synchronous so Slack receives the purple waiting state
        before the potentially slow model or Google request begins. Later calls
        run on the worker and cannot delay the work they describe.
        """
        if not self._channel or not self._thread or self._worker is not None:
            return
        self._set_status(INITIAL_STATUS)
        self._worker = threading.Thread(target=self._run, daemon=True, name="gable-status")
        self._worker.start()

    def stage(self, text: str) -> None:
        """Record the truthful stage to show after the lead-in.

        Args:
            text: Present-tense copy that reads correctly after "Gable". Empty
                input is ignored: only leaving the response context may clear
                the state, after the answer has been posted.

        Raises:
            Nothing.
        """
        if not text:
            return
        with self._stage_lock:
            self._stage = text

    def stop(self) -> None:
        """Stop timed updates and clear the native state without delaying work."""
        self._done.set()
        worker = self._worker
        if worker is None:
            return
        worker.join(timeout=STOP_JOIN_SECONDS)

    def __enter__(self) -> Working:
        """Show the indicator and return the stage reporter."""
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
