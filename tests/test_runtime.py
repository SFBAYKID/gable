"""Tests for the process that joins Socket Mode to the Sheet poller."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from gable.db.schema import apply_migrations, connect
from gable.runtime import RuntimeComponents, serve


class FakeSocket:
    """Records connection lifecycle without opening a network socket."""

    def __init__(self, events: list[str]) -> None:
        """Share the ordered event recorder with the fake runtime."""
        self.events = events

    def connect(self) -> None:
        """Record a non-blocking connection."""
        self.events.append("socket connected")

    def close(self) -> None:
        """Record cleanup."""
        self.events.append("socket closed")


class FakePoller:
    """Records readiness and which thread owns the blocking loop."""

    def __init__(self, events: list[str], *, ready: bool = True, explode: bool = False) -> None:
        """Configure readiness and an optional runtime failure."""
        self.events = events
        self.is_ready = ready
        self.explode = explode

    def ready(self) -> tuple[bool, str]:
        """Return the configured backfill state."""
        self.events.append("poller checked")
        return self.is_ready, "ready" if self.is_ready else "backfill not adopted"

    def run_forever(self) -> int:
        """Record thread ownership, then stop or fail immediately."""
        self.events.append(f"poller on {threading.current_thread().name}")
        if self.explode:
            msg = "poller broke"
            raise RuntimeError(msg)
        return 0


def _components(
    events: list[str], *, ready: bool = True, explode: bool = False
) -> tuple[RuntimeComponents, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    components = RuntimeComponents(
        poller=FakePoller(events, ready=ready, explode=explode),
        socket=FakeSocket(events),
        connection=connection,
    )
    return components, connection


def test_the_poller_stays_on_the_main_thread_after_socket_connects() -> None:
    events: list[str] = []
    components, _ = _components(events)

    assert serve(components) == 0
    assert events == [
        "poller checked",
        "socket connected",
        "poller on MainThread",
        "socket closed",
    ]


def test_backfill_refusal_opens_no_slack_connection() -> None:
    events: list[str] = []
    components, _ = _components(events, ready=False)

    assert serve(components) == 2
    assert events == ["poller checked"]


def test_socket_and_database_close_after_a_runtime_failure() -> None:
    events: list[str] = []
    components, connection = _components(events, explode=True)

    assert serve(components) == 2
    assert events[-1] == "socket closed"
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        closed = True
    else:
        closed = False
    assert closed is True


def test_database_connection_survives_a_thread_handoff(tmp_path: Path) -> None:
    """The old startup crashed before Slack and polling could coexist."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    failures: list[Exception] = []

    def read_from_worker() -> None:
        try:
            connection.execute("SELECT 1").fetchone()
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=read_from_worker)
    worker.start()
    worker.join()
    connection.close()

    assert failures == []
