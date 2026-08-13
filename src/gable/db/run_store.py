"""Persist flyer-run attempts and append-only transitions.

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

#: Every state the runtime may persist. Rejecting typos at the write boundary is
#: important because polling suppresses any submission with a run row; an
#: unknown state would otherwise become permanent, invisible work.
VALID_STATUSES: Final[frozenset[str]] = TERMINAL | ACTIVE | PAUSED

#: An unattended failure may be retried, but never indefinitely.
MAX_RUN_ATTEMPTS: Final[int] = 3

#: A process can die after doing real Drive work but before recording a Slack
#: outcome. The pending marker deliberately lives in an existing column: it is
#: an outbox flag that survives another restart without a schema migration.
INTERRUPTED_REASON: Final[str] = "processing was interrupted before completion"
INTERRUPTED_NOTIFICATION_PENDING: Final[str] = (
    "processing was interrupted before completion; Slack notification pending"
)

#: The corresponding human-owned wait after Slack returns the question's exact
#: timestamp. This stable sentence is safe to expose in bounded thread context.
PHOTO_REPLACEMENT_WAITING: Final[str] = (
    "waiting for the correct property image because the supplied photo conflicts with the listing"
)

_RUN_UPDATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "template_file_id",
        "template_label",
        "output_file_id",
        "output_url",
        "photo_url",
        "photo_source",
        "photo_event_id",
        "ai_generated",
        "ai_enhanced",
        "slack_thread_ts",
        "failure_reason",
    }
)


class RunLimitReachedError(RuntimeError):
    """Raised when opening another run would exceed the hard attempt ceiling."""


class RunAlreadyActiveError(RuntimeError):
    """Raised when another worker owns or a person is resolving this submission."""


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
    photo_event_id: str = ""
    ai_enhanced: bool = False
    approved_warning_codes: str = ""
    pending_warning_code: str = ""

    @property
    def is_terminal(self) -> bool:
        """Return whether ordinary polling must never reprocess this run."""
        return self.status in TERMINAL

    @property
    def is_paused(self) -> bool:
        """Return whether this run is waiting on a person rather than Gable."""
        return self.status in PAUSED


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
        RunAlreadyActiveError: When another worker owns an active attempt or an
            existing attempt is paused for a person.
        sqlite3.Error: On a write or foreign-key failure.
    """
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _transition(connection):
        # The limit check and insert must be one SQLite statement. A prior
        # count-then-insert sequence let two connections both observe attempt
        # two and create attempts three and four. SQLite serializes this write,
        # and the later writer reevaluates the count against the committed row.
        blocking_statuses = tuple(sorted(ACTIVE | PAUSED))
        placeholders = ",".join("?" * len(blocking_statuses))
        inserted = connection.execute(
            f"""
            INSERT INTO runs (run_id, response_row_id, status, created_at, updated_at)
            SELECT ?, ?, 'pending', ?, ?
             WHERE (SELECT COUNT(*) FROM runs WHERE response_row_id = ?) < ?
               AND NOT EXISTS (
                    SELECT 1 FROM runs
                     WHERE response_row_id = ? AND status IN ({placeholders})
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
                *blocking_statuses,
            ),
        )
        if inserted.rowcount != 1:
            blocked = connection.execute(
                f"SELECT 1 FROM runs WHERE response_row_id = ? "
                f"AND status IN ({placeholders}) LIMIT 1",
                (response_row_id, *blocking_statuses),
            ).fetchone()
            if blocked:
                raise RunAlreadyActiveError(
                    f"a run is already active or paused for {response_row_id}"
                )
            if run_attempt_count(connection, response_row_id) >= MAX_RUN_ATTEMPTS:
                raise RunLimitReachedError(f"run attempt limit reached for {response_row_id}")
            raise RunAlreadyActiveError(f"a run could not be opened for {response_row_id}")
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, "pending", "run opened"),
        )
    return RunRow(run_id=run_id, response_row_id=response_row_id, status="pending")


def recover_interrupted_runs(connection: sqlite3.Connection) -> tuple[RunRow, ...]:
    """Pause or fail work left active and return every notice still owed.

    This runs once during production construction, before the new poller or
    Slack listener can own work. A run with an owned thread is paused for review
    so Carmen or Chase can resume the same attempt. A run with no thread is
    failed because nobody has a safe conversation in which to resume it.

    The failure-reason marker is also a tiny durable notification outbox. Runs
    transitioned by an earlier startup remain in the return value until Slack
    confirms their recovery notice and ``acknowledge_interrupted_run`` clears
    the marker. This avoids silently losing the only human-visible outcome when
    Slack is unavailable during startup.

    Returns:
        Recovered rows whose Slack notification is still pending, including
        notices carried over from an earlier startup.
    """
    placeholders = ",".join("?" * len(ACTIVE))
    rows = connection.execute(
        f"SELECT run_id, slack_thread_ts FROM runs "
        f"WHERE status IN ({placeholders}) AND NOT EXISTS ("
        "SELECT 1 FROM run_questions q WHERE q.run_id = runs.run_id "
        "AND q.notification_kind = 'outcome' "
        "AND q.confirmed_at = '' AND q.satisfied_at = '') ORDER BY created_at",
        tuple(sorted(ACTIVE)),
    ).fetchall()
    for row in rows:
        has_thread = bool(str(row["slack_thread_ts"] or "").strip())
        set_status(
            connection,
            str(row["run_id"]),
            "needs_review" if has_thread else "failed",
            "the prior process ended before this run recorded an outcome",
            failure_reason=INTERRUPTED_NOTIFICATION_PENDING,
        )

    return pending_interrupted_runs(connection)


def pending_interrupted_runs(connection: sqlite3.Connection) -> tuple[RunRow, ...]:
    """Return legacy interruption markers not yet moved into the stable-id outbox."""
    pending = connection.execute(
        "SELECT run_id FROM runs WHERE failure_reason = ? ORDER BY created_at, rowid",
        (INTERRUPTED_NOTIFICATION_PENDING,),
    ).fetchall()
    recovered: list[RunRow] = []
    for row in pending:
        run = run_by_id(connection, str(row["run_id"]))
        if run is not None:
            recovered.append(run)
    return tuple(recovered)


def acknowledge_interrupted_run(
    connection: sqlite3.Connection,
    run_id: str,
    notification_ts: str,
) -> bool:
    """Record that Slack confirmed an interrupted-run notice.

    The existing owned root is retained for a threaded recovery. For an
    unthreaded failed run, the new channel notice becomes its root so later
    status questions can still resolve to the exact listing. A stale or double
    acknowledgement is harmless and returns ``False``.

    Args:
        connection: Open Gable database connection.
        run_id: Recovered run whose durable notice was posted.
        notification_ts: Exact timestamp returned by Slack for that message.

    Returns:
        True only when a still-pending notice was acknowledged.

    Raises:
        ValueError: If Slack did not return a message timestamp.
        sqlite3.Error: On a query or write failure.
    """
    confirmed_ts = notification_ts.strip()
    if not confirmed_ts:
        raise ValueError("Slack did not confirm the recovery message")

    now = _now()
    with _transition(connection):
        row = connection.execute(
            "SELECT status, slack_thread_ts FROM runs WHERE run_id = ? AND failure_reason = ?",
            (run_id, INTERRUPTED_NOTIFICATION_PENDING),
        ).fetchone()
        if row is None:
            return False
        thread_root = str(row["slack_thread_ts"] or "").strip() or confirmed_ts
        changed = connection.execute(
            "UPDATE runs SET updated_at = ?, slack_thread_ts = ?, failure_reason = ? "
            "WHERE run_id = ? AND failure_reason = ?",
            (
                now,
                thread_root,
                INTERRUPTED_REASON,
                run_id,
                INTERRUPTED_NOTIFICATION_PENDING,
            ),
        )
        if changed.rowcount != 1:
            return False
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, str(row["status"]), "startup interruption reported in Slack"),
        )
    return True


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
        ValueError: If the status is unknown or a caller supplies a non-run column.
        sqlite3.Error: On a write failure.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown run status: {status!r}")
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
    expected_status: str | None = None,
) -> bool:
    """Atomically move one still-paused run back to pending.

    Args:
        connection: Open Gable database connection.
        run_id: Existing run a person asked Gable to continue.
        detail: Immutable transition note.
        fields: Optional photo or source data that must become current with
            the claim, never in a separate raceable write.
        expected_status: When set, claim only this exact paused state. A Slack
            photo handoff uses ``needs_photo`` so a stale upload cannot resume a
            run that changed to another human-owned pause while the file was
            being downloaded.

    Returns:
        True for the one caller that claimed the pause; False when another
        worker already resumed it or the run is no longer paused.

    Raises:
        ValueError: If a caller supplies a non-run column or a non-paused
            expected status.
        sqlite3.Error: On a write failure.
    """
    updates = dict(fields or {})
    unknown = set(updates) - _RUN_UPDATE_FIELDS
    if unknown:
        raise ValueError(f"not run columns: {sorted(unknown)}")
    if expected_status is not None and expected_status not in PAUSED:
        raise ValueError(f"expected status is not paused: {expected_status!r}")
    now = _now()
    assignments = ", ".join(f"{name} = ?" for name in updates)
    sql = "UPDATE runs SET status = 'pending', updated_at = ?"
    if assignments:
        sql += f", {assignments}"
    allowed_statuses = (expected_status,) if expected_status is not None else tuple(sorted(PAUSED))
    placeholders = ",".join("?" * len(allowed_statuses))
    sql += (
        f" WHERE run_id = ? AND status IN ({placeholders}) "
        "AND NOT EXISTS (SELECT 1 FROM run_questions q WHERE q.run_id = runs.run_id "
        "AND q.confirmed_at = '' AND q.satisfied_at = '')"
    )
    with _transition(connection):
        cursor = connection.execute(
            sql,
            (now, *updates.values(), run_id, *allowed_statuses),
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
    "photo_source, photo_event_id, ai_enhanced, approved_warning_codes, pending_warning_code"
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
    """Return one exact run by its immutable identifier.

    Args:
        connection: Open Gable database connection.
        run_id: Exact run identifier.

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
        photo_event_id=str(row["photo_event_id"] or ""),
        ai_enhanced=bool(row["ai_enhanced"]),
        approved_warning_codes=str(row["approved_warning_codes"] or ""),
        pending_warning_code=str(row["pending_warning_code"] or ""),
    )
