"""Slack-free lifecycle for joining a background listener and the Sheet poller.

Socket Mode's non-blocking ``connect`` method owns its background receiver
threads. The poll loop stays on the interpreter's main thread, where installing
SIGTERM and SIGINT handlers is valid. Database connections are never shared
with Slack event threads; event handlers open their own short-lived connection
from the configured path.

Production client construction lives under ``gable.slackapp.runtime`` so the
pipeline remains importable and runnable when Slack is absent.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("gable.runtime")


class SocketConnection(Protocol):
    """The non-blocking part of Slack's Socket Mode handler."""

    def connect(self) -> None:
        """Open the Socket Mode connection and return."""
        ...

    def close(self) -> None:
        """Close the Socket Mode connection and its worker resources."""
        ...


class PollLoop(Protocol):
    """The safe surface the runtime needs from the poller."""

    def ready(self) -> tuple[bool, str]:
        """Return whether the historical backfill guard has cleared."""
        ...

    def run_forever(self) -> int:
        """Run on the main thread until a signal requests shutdown."""
        ...


class BackgroundLoop(Protocol):
    """A process-lifetime worker that starts and stops without owning signals."""

    def start(self) -> None:
        """Start background work and return immediately."""
        ...

    def close(self) -> None:
        """Stop and join background work."""
        ...


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    """Resources that live for the duration of the production process."""

    poller: PollLoop
    socket: SocketConnection
    connection: sqlite3.Connection
    #: Durable Slack notifications retry even when Sheet polling is disabled.
    notifications: BackgroundLoop | None = None
    #: When false, Slack is served but the Sheet is not watched. The backfill
    #: guard is skipped too: it exists to stop a first boot building every
    #: historical row, and nothing is being built.
    poll_enabled: bool = True


def _wait_for_shutdown() -> None:
    """Block until the process is asked to stop.

    Args:
        None.

    Returns:
        None, once SIGINT or SIGTERM arrives.

    Raises:
        Nothing.
    """
    import contextlib
    import signal
    import threading

    stop = threading.Event()
    for received in (signal.SIGINT, signal.SIGTERM):
        # Off the main thread this raises, and the caller owns shutdown instead.
        with contextlib.suppress(ValueError):
            signal.signal(received, lambda *_: stop.set())
    stop.wait()


def serve(components: RuntimeComponents) -> int:
    """Connect Slack, then run the poller on the main thread until shutdown.

    Args:
        components: Constructed runtime resources.

    Returns:
        Zero after a clean stop, two after a refusal or startup/runtime failure.

    Raises:
        Nothing. The systemd process receives a meaningful exit code.
    """
    connected = False
    notifications_started = False
    try:
        if components.poll_enabled:
            ready, reason = components.poller.ready()
            if not ready:
                logger.error("%s", reason)
                return 2
        components.socket.connect()
        connected = True
        if components.notifications is not None:
            # Mark before start so a partially started worker is still closed if
            # its constructor boundary raises after creating the thread.
            notifications_started = True
            components.notifications.start()
        if not components.poll_enabled:
            logger.info("Slack connected; the Sheet is NOT being watched")
            # Nothing to loop over, so block until a signal arrives. Returning
            # here would close the socket and end the process a moment after
            # announcing it was listening.
            _wait_for_shutdown()
            return 0
        logger.info("Slack connected; watching the Sheet")
        return components.poller.run_forever()
    except Exception:
        logger.exception("Gable runtime stopped unexpectedly")
        return 2
    finally:
        if notifications_started and components.notifications is not None:
            try:
                components.notifications.close()
            except Exception:
                logger.exception("Slack notification recovery did not close cleanly")
        if connected:
            try:
                components.socket.close()
            except Exception:
                logger.exception("Slack did not close cleanly")
        components.connection.close()
