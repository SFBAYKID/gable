"""Turning sheet rows into work, and refusing to do work that was already done.

The single most expensive mistake this system can make is the **backfill**: the
live sheet holds 99 historical submissions, and a first poll that treats them as
new would build 99 flyers, post 99 Slack messages and spend real money, in about
eight minutes, before anyone could stop it.

So `new_submissions()` will not return anything until a cutoff exists. On a
fresh database `adopt_backfill()` records every existing row as already handled
without rendering any of it. That is the guard, and it is deliberately the only
way to get started.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Final

from gable.db import store
from gable.listings.intake import Intake, columns_from_header, from_row, maps_a_response_row
from gable.sheets.client import ReadsRanges, SheetError

#: The full width of the intake form. Reading the whole row keeps the content
#: hash meaningful even for columns Gable ignores today. Wider than the form is
#: harmless — Sheets truncates trailing empty cells — and `Testing_1` already
#: reaches column U, one past where this used to stop.
RESPONSES_RANGE: Final[str] = "A1:Z"

#: How far down to look for the header row. `Form Responses 1` heads row 1 and
#: `Testing_1` heads row 2 under a blank one, so a tab whose header is not in
#: the first few rows is a tab this code has not been shown.
MAX_HEADER_SCAN: Final[int] = 5


@dataclass(frozen=True, slots=True)
class Submission:
    """One row, ready to act on."""

    response_row_id: str
    sheet_row: int
    submitted_at: str
    intake: Intake
    content_hash: str


def _identity(submitted_at: str, agent_email: str, address: str) -> str:
    """A stable id for a submission.

    The deployed database already uses this tuple, so changing the formula
    would reopen every historical row. Row position is deliberately excluded
    because inserting a row above must not renumber later submissions. An edit
    to email or address is reconciled to the prior id by timestamp in
    ``new_submissions`` before anything can be opened as new work.
    """
    basis = f"{submitted_at.strip()}|{agent_email.strip().lower()}|{address.strip().lower()}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _content_hash(row: list[str]) -> str:
    """A hash of the whole row, so an edit to a submission is detectable.

    The identity deliberately ignores most columns; this does not. A form
    response edited in place keeps its timestamp and therefore its identity, and
    without this a substantive correction would be silently swallowed.
    """
    return hashlib.sha256("\x1f".join(row).encode()).hexdigest()[:16]


def find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Locate the header row and read the column layout off it.

    Args:
        rows: The tab's rows, from row 1, as the sheet returns them.

    Returns:
        `(index, columns)` — the zero-based position of the header row, and
        field name to column index for everything below it.

    Raises:
        SheetError: when no row in the first few describes a responses tab.
            Refusing is the point: falling back to fixed positions on a tab
            with a different shape reads real values into the wrong fields,
            which is indistinguishable from a genuine submission downstream.
    """
    for index, row in enumerate(rows[:MAX_HEADER_SCAN]):
        columns = columns_from_header(row)
        if maps_a_response_row(columns):
            return index, columns
    msg = (
        "the responses tab has no header row naming the email, request type and "
        "property address, so I will not guess which column is which"
    )
    raise SheetError(msg)


def submission_from_row(row: list[str], columns: dict[str, int], sheet_row: int) -> Submission:
    """Parse one already-read row into a submission.

    Args:
        row: The raw row.
        columns: Field name to column index, from `columns_from_header`.
        sheet_row: The 1-based sheet row, for pointing a human at it.

    Returns:
        The submission, with the same content-derived identity a poll gives it,
        so starting a row by hand and polling it cannot produce two runs.

    Raises:
        Nothing.
    """
    intake = from_row(row, columns)
    submitted_at = row[0].strip() if row else ""
    if not submitted_at:
        msg = f"row {sheet_row} has no submission timestamp, so I cannot identify it safely"
        raise SheetError(msg)
    return Submission(
        response_row_id=_identity(submitted_at, intake.agent_email, intake.address),
        sheet_row=sheet_row,
        submitted_at=submitted_at,
        intake=intake,
        content_hash=_content_hash(row),
    )


def read_submissions(client: ReadsRanges, tab: str) -> list[Submission]:
    """Every row on the responses tab, parsed.

    Args:
        client: A read-only sheet client.
        tab: The responses tab name.

    Returns:
        Submissions in sheet order. Everything up to and including the header
        row is skipped.

    Raises:
        SheetError: if the tab cannot be read, or has no recognisable header.
    """
    rows = client.read(f"'{tab}'!{RESPONSES_RANGE}")
    header_index, columns = find_header(rows)
    out: list[Submission] = []
    # `start` is the 1-based sheet row of the first row after the header, so a
    # blank leading row cannot shift what Gable tells a human to look at.
    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue
        out.append(submission_from_row(row, columns, offset))
    timestamps = [submission.submitted_at for submission in out]
    if len(timestamps) != len(set(timestamps)):
        msg = (
            "two response rows have the same submission timestamp, so I cannot "
            "tell them apart safely"
        )
        raise SheetError(msg)
    return out


def adopt_backfill(connection: Connection, submissions: list[Submission]) -> int:
    """Record existing rows as already handled, without building anything.

    This is how a fresh deployment starts. Every current row is written to the
    database with a terminal run, so the first real poll sees only what arrives
    afterwards.

    Args:
        connection: An open database connection.
        submissions: Everything currently on the sheet.

    Returns:
        How many rows were adopted.

    Raises:
        sqlite3.Error: on a write failure.
    """
    adopted = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for submission in submissions:
            store.record_submission(
                connection,
                submission.response_row_id,
                submission.sheet_row,
                submission.submitted_at,
                submission.intake,
                submission.content_hash,
            )
            # Also repairs a database left by the old non-transactional
            # implementation, where a crash could store the submission but die
            # before its terminal history run was opened.
            if store.has_been_handled(connection, submission.response_row_id):
                continue
            run = store.start_run(connection, submission.response_row_id)
            store.set_status(
                connection,
                run.run_id,
                "skipped",
                "adopted as backfill on first run; it predates Gable and was not built",
            )
            adopted += 1
        _mark_backfilled(connection)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return adopted


def _mark_backfilled(connection: Connection) -> None:
    """Record that the backfill has been adopted, so it happens exactly once."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS gable_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO gable_state (key, value) VALUES ('backfill_adopted_at', ?)"
        " ON CONFLICT(key) DO NOTHING",
        (datetime.now(UTC).isoformat(),),
    )


def backfill_adopted(connection: Connection) -> bool:
    """Whether the historical rows have been adopted.

    Args:
        connection: An open database connection.

    Returns:
        True once `adopt_backfill` has run.

    Raises:
        sqlite3.Error: on a query failure.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS gable_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    row = connection.execute(
        "SELECT value FROM gable_state WHERE key = 'backfill_adopted_at'"
    ).fetchone()
    return row is not None


def new_submissions(connection: Connection, submissions: list[Submission]) -> list[Submission]:
    """The rows that still need building.

    Args:
        connection: An open database connection.
        submissions: Everything currently on the sheet.

    Returns:
        Submissions that have never had a run attempt. Paused and failed work
        resumes or retries only through an explicit operator action. Empty
        until the backfill has been adopted — a poller that has not been told
        which rows are history must not guess.

    Raises:
        sqlite3.Error: on a query failure.
    """
    if not backfill_adopted(connection):
        return []
    pending: list[Submission] = []
    for submission in submissions:
        submission = reconcile_identity(connection, submission)
        store.record_submission(
            connection,
            submission.response_row_id,
            submission.sheet_row,
            submission.submitted_at,
            submission.intake,
            submission.content_hash,
        )
        if store.has_been_handled(connection, submission.response_row_id):
            continue
        pending.append(submission)
    return pending


def reconcile_identity(connection: Connection, submission: Submission) -> Submission:
    """Preserve the deployed id after editable identity fields are corrected.

    Google Forms keeps the submission timestamp stable when an operator edits
    email or address, while Gable's deployed hash includes both editable
    fields. Every entry path, including the manual row runner, must therefore
    resolve the timestamp before storing or opening work.
    """
    prior_id = store.response_id_for_timestamp(connection, submission.submitted_at)
    if prior_id and prior_id != submission.response_row_id:
        return replace(submission, response_row_id=prior_id)
    return submission


def find_salesperson(connection: Connection, email: str) -> dict[str, str]:
    """Look up an agent by email.

    Args:
        connection: An open database connection.
        email: The submitting agent's address.

    Returns:
        Their row as a dict, or empty if unknown. An unknown agent is a question
        for Carmen, never a reason to guess a template.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT email, first_name, last_name, phone, template, headshot_url, brokerage_url"
        " FROM salespeople WHERE email = ?",
        (email.strip().lower(),),
    ).fetchone()
    return dict(row) if row else {}
