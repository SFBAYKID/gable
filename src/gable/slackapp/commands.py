"""Implement the operator-only ``/gable`` command surface.

Slash commands arrive on Bolt worker threads. They may read through a fresh
SQLite connection, but they never call the poller, Google clients, or runner
directly. Mutating work is queued onto the poller's main thread, preserving the
same client and database ownership as scheduled work.

The slash payload has no message timestamp to which Slack's native assistant
status can attach, so these commands acknowledge immediately and answer
ephemerally. User-triggered work that has a real message thread continues to use
the native waiting sequence in ``status.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from gable.db import store
from gable.db.schema import connect
from gable.voice import is_clean, quote_rail, safe, strip_to_plain

logger = logging.getLogger("gable.slack.commands")

_PACIFIC = ZoneInfo("America/Los_Angeles")
_ACTIVE_RUN_STATUSES = frozenset({"pending", "building"})


class ControlsPolling(Protocol):
    """Thread-safe poller controls used by slash commands."""

    @property
    def is_paused(self) -> bool:
        """Return whether scheduled polling is paused."""
        ...

    @property
    def last_poll_at(self) -> datetime | None:
        """Return the latest completed poll in this process."""
        ...

    def request_pass(self) -> None:
        """Queue an immediate Sheet pass."""
        ...

    def pause(self) -> None:
        """Pause scheduled passes."""
        ...

    def resume(self) -> None:
        """Resume scheduled passes and queue a catch-up pass."""
        ...

    def queue_retry(self, run_id: str) -> bool:
        """Queue a fresh attempt based on an existing run."""
        ...

    def queue_resume(self, run_id: str) -> bool:
        """Queue an existing paused run for a source refresh and recheck."""
        ...


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One or more ephemeral replies to a slash command."""

    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommandService:
    """Parse operator commands and delegate to bounded runtime controls."""

    db_path: Path
    poller: ControlsPolling
    list_templates: Callable[[], list[str]]
    polling_configured: bool = True

    def handle(self, text: str) -> CommandResult:
        """Execute one ``/gable`` subcommand.

        Args:
            text: Everything after ``/gable`` in Slack's command payload.

        Returns:
            Safe ephemeral reply messages.

        Raises:
            Nothing. A command failure becomes an actionable plain sentence.
        """
        parts = text.strip().split()
        if not parts:
            return self._help()
        command = parts[0].casefold()
        try:
            if command == "status" and len(parts) == 1:
                return self._status()
            if command == "run" and len(parts) == 1:
                return self._run_now()
            if command == "retry" and len(parts) == 2:
                return self._retry(parts[1])
            if command == "templates" and len(parts) == 1:
                return self._templates()
            if command == "pause" and len(parts) == 1:
                return self._pause()
            if command == "resume" and len(parts) == 1:
                return self._resume()
            return self._help()
        except Exception:
            logger.exception("a Gable slash command could not complete")
            return CommandResult(
                (
                    "I could not confirm whether that command completed. Check Gable's "
                    "status before trying it again.",
                )
            )

    def _status(self) -> CommandResult:
        """Report latest-listing counts and the most recent poll attempt."""
        connection = connect(self.db_path)
        try:
            counts = store.status_counts(connection)
        finally:
            connection.close()
        polling = (
            "disabled in configuration"
            if not self.polling_configured
            else "paused"
            if self.poller.is_paused
            else "active"
        )
        facts = quote_rail(
            [
                f"Pending  {counts.pending}",
                f"Ready  {counts.ready}",
                f"Failed  {counts.failed}",
                f"Polling  {polling}",
                f"Last poll  {self._when(self.poller.last_poll_at)}",
            ]
        )
        return CommandResult((safe(facts),))

    def _run_now(self) -> CommandResult:
        """Queue a source refresh and recheck every current paused listing."""
        if not self.polling_configured:
            return CommandResult(
                ("Polling is disabled in configuration, so I did not start a cycle.",)
            )
        connection = connect(self.db_path)
        try:
            waiting = store.paused_runs(connection)
        finally:
            connection.close()
        queued = sum(self.poller.queue_resume(run.run_id) for run in waiting)
        self.poller.request_pass()
        if not waiting:
            return CommandResult(
                ("I queued a poll cycle now. There are no paused listings to recheck.",)
            )
        if queued == len(waiting):
            noun = "listing" if queued == 1 else "listings"
            return CommandResult(
                (f"I queued a poll cycle and {queued} paused {noun} for recheck.",)
            )
        return CommandResult(
            (
                f"I queued a poll cycle and {queued} paused listings for recheck. "
                f"I left {len(waiting) - queued} out because they were already queued "
                "or the operator queue was full.",
            )
        )

    def _retry(self, run_id: str) -> CommandResult:
        """Validate and queue one explicit fresh attempt."""
        if not self.polling_configured:
            return CommandResult(
                ("Polling is disabled in configuration, so I did not queue a retry.",)
            )
        connection = connect(self.db_path)
        try:
            source = store.run_by_id(connection, run_id)
            if source is None:
                return CommandResult(
                    (f"I could not find a run named {run_id}, so I did not retry anything.",)
                )
            latest = store.latest_run(connection, source.response_row_id)
            if latest is None or latest.run_id != source.run_id:
                current_id = latest.run_id if latest is not None else "the current run"
                return CommandResult(
                    (
                        f"{run_id} is not this listing's latest run, so I did not queue an "
                        f"older state. Check {current_id} instead.",
                    )
                )
            if source.status in _ACTIVE_RUN_STATUSES:
                return CommandResult(
                    (
                        f"{run_id} is still active, so I did not start a second copy of "
                        "the same work.",
                    )
                )
            if (
                store.run_attempt_count(connection, source.response_row_id)
                >= store.MAX_RUN_ATTEMPTS
            ):
                return CommandResult(
                    (
                        f"{run_id} has already reached the three-attempt limit, so I "
                        "did not start it again.",
                    )
                )
            if store.load_submission(connection, source.response_row_id) is None:
                return CommandResult(
                    (
                        f"I found {run_id} but not its request details, so I did not "
                        "start another attempt.",
                    )
                )
        finally:
            connection.close()
        if not self.poller.queue_retry(run_id):
            return CommandResult(
                (f"{run_id} is already queued, so I did not add it a second time.",)
            )
        return CommandResult(
            (
                f"I queued a fresh attempt for {run_id}. I will reread the request and "
                "current template first.",
            )
        )

    def _templates(self) -> CommandResult:
        """List every current Generic Templates design without truncating names."""
        names = sorted(
            {_display_name(name) for name in self.list_templates() if name.strip()},
            key=str.casefold,
        )
        if not names:
            return CommandResult(
                ("I did not find any Google Slides designs in Generic Templates.",)
            )
        chunks: list[list[str]] = []
        current: list[str] = []
        current_chars = 0
        for name in names:
            added = len(name) + 6
            if current and current_chars + added > 420:
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(name)
            current_chars += added
        if current:
            chunks.append(current)
        messages: list[str] = []
        for index, chunk in enumerate(chunks):
            heading = (
                f"{len(names)} designs are filed in Generic Templates."
                if index == 0
                else "More filed designs."
            )
            messages.append(safe(f"{heading}\n{quote_rail(chunk)}"))
        return CommandResult(tuple(messages))

    def _pause(self) -> CommandResult:
        """Pause only scheduled Sheet polling, leaving conversations active."""
        if not self.polling_configured:
            return CommandResult(("Polling is already disabled in configuration.",))
        if self.poller.is_paused:
            return CommandResult(("Polling is already paused.",))
        self.poller.pause()
        return CommandResult(
            ("Polling is paused. Flyer edits and existing Slack threads still work.",)
        )

    def _resume(self) -> CommandResult:
        """Resume scheduled polling and immediately catch up."""
        if not self.polling_configured:
            return CommandResult(
                ("Polling is disabled in configuration, so I could not resume it here.",)
            )
        if not self.poller.is_paused:
            return CommandResult(("Polling is already active.",))
        self.poller.resume()
        return CommandResult(("Polling is active again. I also queued a catch-up cycle.",))

    @staticmethod
    def _when(instant: datetime | None) -> str:
        """Format one poll timestamp explicitly in Pacific time."""
        if instant is None:
            return "not yet since startup"
        local = instant.astimezone(_PACIFIC)
        hour = local.strftime("%I").lstrip("0") or "12"
        return f"{local.strftime('%b')} {local.day} at {hour}:{local.strftime('%M %p')} Pacific"

    @staticmethod
    def _help() -> CommandResult:
        """Return the complete command grammar without code styling."""
        return CommandResult(
            (
                "Use /gable status, /gable run, /gable retry followed by a run ID, "
                "/gable templates, /gable pause, or /gable resume.",
            )
        )


def _display_name(name: str) -> str:
    """Make a Drive file name safe to echo into a Slack response."""
    cleaned = strip_to_plain(" ".join(name.split()))
    return cleaned if cleaned and is_clean(cleaned) else "Unnamed design"
