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


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    """Resources that live for the duration of the production process."""

    poller: PollLoop
    socket: SocketConnection
    connection: sqlite3.Connection


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
    try:
        ready, reason = components.poller.ready()
        if not ready:
            logger.error("%s", reason)
            return 2
        components.socket.connect()
        connected = True
        logger.info("Slack connected; watching the Sheet")
        return components.poller.run_forever()
    except Exception:
        logger.exception("Gable runtime stopped unexpectedly")
        return 2
    finally:
        if connected:
            try:
                components.socket.close()
            except Exception:
                logger.exception("Slack did not close cleanly")
        components.connection.close()
