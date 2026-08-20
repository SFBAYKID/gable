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
        "awaiting_photo",
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
    #: Whether the last ask sent to this run's thread included the property
    #: photograph. Read alongside `status`, never instead of it: a run blocked
    #: on a design a person must widen parks in `needs_template` and is still
    #: owed the photo it asked for in the same message.
    awaiting_photo: bool = False

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
    "photo_source, photo_event_id, ai_enhanced, approved_warning_codes, "
    "pending_warning_code, awaiting_photo"
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
        awaiting_photo=bool(row["awaiting_photo"]),
    )


#: Recorded in `runs.approved_warning_codes` when a person has said to build
#: without the listing values Gable could not find. Chase's rule, 2026-08-13:
#: the sheet is what there is, so a gap is Carmen's decision rather than a dead
#: end — she either supplies the value or says build it and she will fill it in.
BUILD_WITH_BLANKS: Final[str] = "build_with_blank_fields"


def set_awaiting_photo(
    connection: sqlite3.Connection,
    run_id: str,
    awaiting: bool,
) -> None:
    """Record whether the ask about to go out includes the property photograph.

    Written WITHOUT moving the run: the ask that follows owns the status
    transition, and a second writer touching `status` here would race it. It
    still writes a `run_events` row inside the same savepoint, because the
    module contract is that every change to a run is explainable from the log --
    and "did this run ask for a photograph, and when" is exactly the fact the
    next recurrence of this bug will need.

    This exists because one status cannot hold two truths. A run that needs a
    design widened AND needs its photo parks in `needs_template` -- the widening
    is the part a person must do outside Slack -- while the same message says
    "Separately, can you send me the property photo?". Without this column the
    upload answering that sentence is refused as unexpected.

    Args:
        connection: An open connection.
        run_id: The run being asked about.
        awaiting: Whether the outgoing message asks for the photograph.

    Raises:
        sqlite3.Error: on a write failure.
    """
    now = _now()
    with _transition(connection):
        changed = connection.execute(
            "UPDATE runs SET awaiting_photo = ?, updated_at = ? "
            "WHERE run_id = ? AND awaiting_photo != ?",
            (1 if awaiting else 0, now, run_id, 1 if awaiting else 0),
        )
        if changed.rowcount != 1:
            # Either the run is gone or the flag already says this. Neither is
            # an error, and neither deserves an event row claiming a change.
            return
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (
                run_id,
                now,
                "awaiting_photo",
                "the outgoing ask includes the property photograph"
                if awaiting
                else "the outgoing ask does not include the property photograph",
            ),
        )


def approve_blank_fields(
    connection: sqlite3.Connection,
    run_id: str,
    detail: str = "approved building with the unknown values left blank",
) -> None:
    """Record that a flyer may be built with its unknown values left blank.

    The release is recorded WITHOUT moving the run. It used to set the status to
    ``pending``, which put the run outside `PAUSED` and made the very next step —
    `claim_paused_run` — fail, so every release answered "this listing is
    already being rechecked" and nothing was ever built. The run stays paused
    here and the caller claims it exactly as it would any other resume.

    Args:
        connection: An open connection.
        run_id: The run being released.
        detail: Immutable audit note for the event row.

    Raises:
        sqlite3.Error: on a write failure.
    """
    row = connection.execute(
        "SELECT status, approved_warning_codes FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return
    codes = {code for code in str(row["approved_warning_codes"] or "").split(",") if code}
    if BUILD_WITH_BLANKS in codes:
        return
    codes.add(BUILD_WITH_BLANKS)
    now = _now()
    with _transition(connection):
        connection.execute(
            "UPDATE runs SET approved_warning_codes = ?, updated_at = ? WHERE run_id = ?",
            (",".join(sorted(codes)), now, run_id),
        )
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, str(row["status"]), detail),
        )


def photo_was_rejected(connection: sqlite3.Connection, run_id: str) -> bool:
    """Whether a check has refused the photograph this run currently holds.

    `runs.photo_url` is kept after a refusal as audit evidence for the image
    that was rejected, NOT as permission to reuse it. Anything that reads the
    column as "this run has a usable photograph" is wrong; ask this instead.

    Args:
        connection: An open connection.
        run_id: The run being asked about.

    Returns:
        True when the run holds a photo URL and its own history records that a
        check refused it and asked for another.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT 1 FROM runs r WHERE r.run_id = ? AND r.photo_url != '' AND EXISTS ("
        "  SELECT 1 FROM run_events e WHERE e.run_id = r.run_id"
        "  AND (e.detail LIKE '%photo%' OR e.detail LIKE '%image%')"
        "  AND (e.detail LIKE '%replace%' OR e.detail LIKE '%contradict%'"
        "       OR e.detail LIKE '%conflict%' OR e.detail LIKE '%reject%')"
        ") LIMIT 1",
        (run_id,),
    ).fetchone()
    return row is not None


def blanks_approved(connection: sqlite3.Connection, run_id: str) -> bool:
    """Whether this run may build with its unknown values left blank.

    Args:
        connection: An open connection.
        run_id: The run being built.

    Returns:
        True once a person has released it. Approval is per run, so the next
        listing asks its own question rather than inheriting this decision.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT approved_warning_codes FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row:
        return False
    return BUILD_WITH_BLANKS in str(row["approved_warning_codes"] or "").split(",")


def reopen_for_rebuild(
    connection: sqlite3.Connection,
    run_id: str,
    action_id: str,
    thread_ts: str,
) -> bool:
    """Atomically return a finished run to a state its own thread can rebuild.

    A delivered run is terminal, so `claim_paused_run` refuses it and "run it
    again" answered "this listing is already being rechecked" — the one thing
    Carmen is most likely to ask for after reading a flyer. This moves it back
    to a human-owned pause inside the same transaction as its event claim, so a
    duplicate Slack delivery cannot rebuild the same flyer twice.

    Args:
        connection: An open database connection.
        run_id: The finished run being reopened.
        action_id: Stable Slack identity of the request, for the claim.
        thread_ts: The thread the request arrived in; it must be the run's own.

    Returns:
        True for the one caller that reopened it. False when the run is not
        finished, belongs to another thread, or this request was already
        handled.

    Raises:
        sqlite3.IntegrityError: if the claim is won and the transition is then
            lost, which would mean two writers disagreed inside one savepoint.
    """
    clean_action_id = action_id.strip()
    root = thread_ts.strip()
    if not clean_action_id or not root:
        return False
    with _transition(connection):
        run = connection.execute(
            "SELECT status, slack_thread_ts FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if (
            run is None
            or str(run["status"]) not in {"delivered", "needs_review"}
            or str(run["slack_thread_ts"] or "") != root
        ):
            return False
        claimed = connection.execute(
            """
            INSERT OR IGNORE INTO slack_event_claims (
                route, event_id, subject_id, thread_ts, fingerprint, claimed_at
            ) VALUES ('run_action', ?, ?, ?, 'rebuild:again', ?)
            """,
            (clean_action_id, run_id, root, _now()),
        )
        if claimed.rowcount != 1:
            return False
        now = _now()
        # needs_review is the paused state meaning "a person owns this now".
        # The run sits here only until the caller claims it in the same request.
        changed = connection.execute(
            "UPDATE runs SET status = 'needs_review', updated_at = ?, failure_reason = '' "
            "WHERE run_id = ? AND status IN ('delivered', 'needs_review')",
            (now, run_id),
        )
        if changed.rowcount != 1:
            raise sqlite3.IntegrityError("the rebuild request lost its run claim")
        connection.execute(
            "INSERT INTO run_events (run_id, at, status, detail) VALUES (?,?,?,?)",
            (run_id, now, "needs_review", "reopened to build this flyer again"),
        )
        connection.execute(
            "UPDATE slack_event_claims SET completed_at = ?, detail = ? "
            "WHERE route = 'run_action' AND event_id = ? AND subject_id = ?",
            (now, "run reopened for a rebuild", clean_action_id, run_id),
        )
    return True
