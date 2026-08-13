"""Persist flyer-run attempts, transitions, and operator-facing counts.

Run state is a separate concern from submissions, cached facts, and spend.  It
also has the highest write frequency in the database layer, so keeping it here
makes the retry ceiling and append-only event contract reviewable together.
Every transition writes both the current ``runs`` row and one ``run_events``
entry in the same autocommit connection.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

#: Statuses that mean a submission is finished and must never be rebuilt by
#: ordinary polling.
TERMINAL: Final[frozenset[str]] = frozenset(("delivered", "failed", "skipped"))

#: States owned by a worker right now. A second worker must never open another
#: attempt while either is current.
ACTIVE: Final[frozenset[str]] = frozenset(("pending", "building"))

#: Statuses that mean a run is waiting on a human. They resume rather than open
#: a fresh attempt.
PAUSED: Final[frozenset[str]] = frozenset(
    {"needs_photo", "needs_info", "needs_template", "needs_review"}
)

#: An unattended failure may be retried, but never indefinitely.
MAX_RUN_ATTEMPTS: Final[int] = 3

_RUN_UPDATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "template_file_id",
        "template_label",
        "output_file_id",
        "output_url",
        "photo_url",
        "photo_source",
        "ai_generated",
        "ai_enhanced",
        "slack_thread_ts",
        "failure_reason",
    }
)


class RunLimitReachedError(RuntimeError):
    """Raised when opening another run would exceed the hard attempt ceiling."""


class RunAlreadyActiveError(RuntimeError):
    """Raised when another worker already owns this submission."""


def _now() -> str:
    """Return the UTC timestamp format used by every database write."""
    return datetime.now(UTC).isoformat()


@contextmanager
def _transition(connection: sqlite3.Connection) -> Iterator[None]:
    """Keep a current run row and its immutable event in one transaction.

    A savepoint works both in autocommit mode and inside a caller-owned
    transaction. Without it, an event-write failure can leave the mutable row
    advanced with no matching explanation in ``run_events``.
    """
    connection.execute("SAVEPOINT gable_run_transition")
    try:
        yield
    except Exception:
        connection.execute("ROLLBACK TO gable_run_transition")
        connection.execute("RELEASE gable_run_transition")
        raise
    connection.execute("RELEASE gable_run_transition")


@dataclass(frozen=True, slots=True)
class RunRow:
    """One attempt at turning a submission into a post."""

    run_id: str
    response_row_id: str
    status: str
    output_file_id: str = ""
    output_url: str = ""
    slack_thread_ts: str = ""
    failure_reason: str = ""
    template_file_id: str = ""
    template_label: str = ""
    #: The fitted, published hero photo, once one has been attached.
    photo_url: str = ""
    photo_source: str = ""
    ai_enhanced: bool = False

    @property
    def is_terminal(self) -> bool:
        """Return whether ordinary polling must never reprocess this run."""
        return self.status in TERMINAL

    @property
    def is_paused(self) -> bool:
        """Return whether this run is waiting on a person rather than Gable."""
        return self.status in PAUSED


@dataclass(frozen=True, slots=True)
class RunCounts:
    """Latest-listing counts shown by the operator status command."""

    pending: int = 0
    ready: int = 0
    failed: int = 0


def run_attempt_count(connection: sqlite3.Connection, response_row_id: str) -> int:
    """Count attempts already recorded for one submission.

    Args:
        connection: Open Gable database connection.
        response_row_id: Submission identity.

    Returns:
        Number of run rows for that submission.

    Raises:
        sqlite3.Error: On a query failure.
    """
    row = connection.execute(
        "SELECT COUNT(*) AS attempts FROM runs WHERE response_row_id = ?",
        (response_row_id,),
    ).fetchone()
    return int(row["attempts"])


def start_run(connection: sqlite3.Connection, response_row_id: str) -> RunRow:
    """Open one bounded attempt for a stored submission.

    Args:
        connection: Open Gable database connection.
        response_row_id: Submission this run belongs to.

    Returns:
        New pending run.

    Raises:
        RunLimitReachedError: When three attempts already exist.
        RunAlreadyActiveError: When another worker owns an active attempt.
        sqlite3.Error: On a write or foreign-key failure.
    """
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _transition(connection):
        # The limit check and insert must be one SQLite statement. A prior
        # count-then-insert sequence let two connections both observe attempt
        # two and create attempts three and four. SQLite serializes this write,
        # and the later writer reevaluates the count against the committed row.
        inserted = connection.execute(
            """
            INSERT INTO runs (run_id, response_row_id, status, created_at, updated_at)
            SELECT ?, ?, 'pending', ?, ?
             WHERE (SELECT COUNT(*) FROM runs WHERE response_row_id = ?) < ?
               AND NOT EXISTS (
                    SELECT 1 FROM runs
                     WHERE response_row_id = ? AND status IN ('pending', 'building')
               )
            """,
            (
                run_id,
                response_row_id,
                now,
                now,
                response_row_id,
                MAX_RUN_ATTEMPTS,
                response_row_id,
            ),
        )
        if inserted.rowcount != 1:
            if run_attempt_count(connection, response_row_id) >= MAX_RUN_ATTEMPTS:
                raise RunLimitReachedError(f"run attempt limit reached for {response_row_id}")
            raise RunAlreadyActiveError(f"a run is already active for {response_row_id}")
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, "pending", "run opened"),
        )
    return RunRow(run_id=run_id, response_row_id=response_row_id, status="pending")


def recover_interrupted_runs(connection: sqlite3.Connection) -> int:
    """Mark work left active by the previous process as explicitly failed.

    This runs once during production construction, before the new poller or
    Slack listener can own work. Ordinary polling never treats a pending row as
    permission to retry; after a restart the operator can inspect and retry the
    explained failure without risking two live workers on one listing.
    """
    placeholders = ",".join("?" * len(ACTIVE))
    rows = connection.execute(
        f"SELECT run_id FROM runs WHERE status IN ({placeholders}) ORDER BY created_at",
        tuple(sorted(ACTIVE)),
    ).fetchall()
    for row in rows:
        set_status(
            connection,
            str(row["run_id"]),
            "failed",
            "the prior process ended before this run recorded an outcome",
            failure_reason="processing was interrupted before completion",
        )
    return len(rows)


def set_status(
    connection: sqlite3.Connection,
    run_id: str,
    status: str,
    detail: str = "",
    **fields: str | int,
) -> None:
    """Move a run and append the matching immutable transition event.

    Args:
        connection: Open Gable database connection.
        run_id: Run to update.
        status: New state-machine status.
        detail: Human-readable audit note.
        **fields: Whitelisted run columns updated with the transition.

    Raises:
        ValueError: If a caller supplies a non-run column.
        sqlite3.Error: On a write failure.
    """
    unknown = set(fields) - _RUN_UPDATE_FIELDS
    if unknown:
        raise ValueError(f"not run columns: {sorted(unknown)}")

    now = _now()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    sql = "UPDATE runs SET status = ?, updated_at = ?"
    if assignments:
        sql += f", {assignments}"
    sql += " WHERE run_id = ?"
    with _transition(connection):
        connection.execute(sql, (status, now, *fields.values(), run_id))
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, status, detail),
        )


def claim_paused_run(
    connection: sqlite3.Connection,
    run_id: str,
    fields: Mapping[str, str | int] | None = None,
    *,
    detail: str = "resumed from its Slack thread",
) -> bool:
    """Atomically move one still-paused run back to pending.

    Args:
        connection: Open Gable database connection.
        run_id: Existing run a person asked Gable to continue.
        detail: Immutable transition note.
        fields: Optional photo or source data that must become current with
            the claim, never in a separate raceable write.

    Returns:
        True for the one caller that claimed the pause; False when another
        worker already resumed it or the run is no longer paused.

    Raises:
        ValueError: If a caller supplies a non-run column.
        sqlite3.Error: On a write failure.
    """
    updates = dict(fields or {})
    unknown = set(updates) - _RUN_UPDATE_FIELDS
    if unknown:
        raise ValueError(f"not run columns: {sorted(unknown)}")
    now = _now()
    assignments = ", ".join(f"{name} = ?" for name in updates)
    sql = "UPDATE runs SET status = 'pending', updated_at = ?"
    if assignments:
        sql += f", {assignments}"
    placeholders = ",".join("?" * len(PAUSED))
    sql += f" WHERE run_id = ? AND status IN ({placeholders})"
    with _transition(connection):
        cursor = connection.execute(
            sql,
            (now, *updates.values(), run_id, *sorted(PAUSED)),
        )
        if cursor.rowcount != 1:
            return False
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, "pending", detail),
        )
    return True


_RUN_COLUMNS: Final[str] = (
    "run_id, response_row_id, status, template_file_id, template_label, "
    "output_file_id, output_url, slack_thread_ts, failure_reason, photo_url, "
    "photo_source, ai_enhanced"
)


def latest_run(connection: sqlite3.Connection, response_row_id: str) -> RunRow | None:
    """Return the newest attempt for a submission, if one exists."""
    row = connection.execute(
        f"SELECT {_RUN_COLUMNS} FROM runs "
        "WHERE response_row_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (response_row_id,),
    ).fetchone()
    return _to_run(row) if row else None


def run_by_id(connection: sqlite3.Connection, run_id: str) -> RunRow | None:
    """Return one exact run for an operator retry request.

    Args:
        connection: Open Gable database connection.
        run_id: Exact run identifier supplied by Chase or Carmen.

    Returns:
        Matching run, or ``None``.

    Raises:
        sqlite3.Error: On a query failure.
    """
    row = connection.execute(
        f"SELECT {_RUN_COLUMNS} FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return _to_run(row) if row else None


def run_for_thread(connection: sqlite3.Connection, thread_ts: str) -> RunRow | None:
    """Resolve an owned Slack thread to its newest run."""
    row = connection.execute(
        f"SELECT {_RUN_COLUMNS} FROM runs "
        "WHERE slack_thread_ts = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (thread_ts,),
    ).fetchone()
    return _to_run(row) if row else None


def paused_runs(connection: sqlite3.Connection) -> list[RunRow]:
    """Return each submission's latest run when it is human-paused.

    An older paused attempt must not re-enter after a newer attempt has already
    failed, skipped, or delivered that submission.
    """
    placeholders = ",".join("?" * len(PAUSED))
    rows = connection.execute(
        f"""
        WITH ranked AS (
            SELECT {_RUN_COLUMNS}, created_at, rowid,
                   ROW_NUMBER() OVER (
                       PARTITION BY response_row_id
                       ORDER BY created_at DESC, rowid DESC
                   ) AS position
              FROM runs
        )
        SELECT {_RUN_COLUMNS}
          FROM ranked
         WHERE position = 1 AND status IN ({placeholders})
         ORDER BY created_at, rowid
        """,
        tuple(sorted(PAUSED)),
    ).fetchall()
    return [_to_run(row) for row in rows]


def status_counts(connection: sqlite3.Connection) -> RunCounts:
    """Count listings by their latest attempt for ``/gable status``.

    Older failed attempts do not inflate the failure count after a later run is
    delivered. ``skipped`` backfill rows are history and appear in no count.

    Args:
        connection: Open Gable database connection.

    Returns:
        Pending, delivered-ready, and failed listing counts.

    Raises:
        sqlite3.Error: On a query failure.
    """
    row = connection.execute(
        """
        WITH ranked AS (
            SELECT status,
                   ROW_NUMBER() OVER (
                       PARTITION BY response_row_id
                       ORDER BY created_at DESC, rowid DESC
                   ) AS position
              FROM runs
        ), latest AS (
            SELECT status FROM ranked WHERE position = 1
        )
        SELECT
            COALESCE(SUM(CASE
                WHEN status NOT IN ('delivered', 'failed', 'skipped') THEN 1 ELSE 0 END), 0
            ) AS pending,
            COALESCE(SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END), 0) AS ready,
            COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed
          FROM latest
        """
    ).fetchone()
    if row is None:
        return RunCounts()
    return RunCounts(
        pending=int(row["pending"]),
        ready=int(row["ready"]),
        failed=int(row["failed"]),
    )


def _to_run(row: sqlite3.Row) -> RunRow:
    """Turn one SQLite row into the immutable public run record."""
    return RunRow(
        run_id=str(row["run_id"]),
        response_row_id=str(row["response_row_id"]),
        status=str(row["status"]),
        output_file_id=str(row["output_file_id"] or ""),
        output_url=str(row["output_url"] or ""),
        slack_thread_ts=str(row["slack_thread_ts"] or ""),
        failure_reason=str(row["failure_reason"] or ""),
        template_file_id=str(row["template_file_id"] or ""),
        template_label=str(row["template_label"] or ""),
        photo_url=str(row["photo_url"] or ""),
        photo_source=str(row["photo_source"] or ""),
        ai_enhanced=bool(row["ai_enhanced"]),
    )
