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

from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Final

from gable.db import store
from gable.listings.intake import columns_from_header, from_row, maps_a_response_row
from gable.sheets.client import ReadsRanges, SheetError
from gable.sheets.identity import Submission as Submission
from gable.sheets.identity import content_hash, legacy_identity, source_identity
from gable.sheets.identity import reconcile_submissions as _reconcile_submissions
from gable.sheets.identity import remember_source_rows as _remember_source_rows

#: The full width of the intake form. Reading the whole row keeps the content
#: hash meaningful even for columns Gable ignores today. Wider than the form is
#: harmless — Sheets truncates trailing empty cells — and `Testing_1` already
#: reaches column U, one past where this used to stop.
RESPONSES_RANGE: Final[str] = "A1:Z"

#: How far down to look for the header row. `Form Responses 1` heads row 1 and
#: `Testing_1` heads row 2 under a blank one, so a tab whose header is not in
#: the first few rows is a tab this code has not been shown.
MAX_HEADER_SCAN: Final[int] = 5


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


def submission_from_row(
    row: list[str],
    columns: dict[str, int],
    sheet_row: int,
    source_tab: str = "",
) -> Submission:
    """Parse one already-read row into a submission.

    Args:
        row: The raw row.
        columns: Field name to column index, from `columns_from_header`.
        sheet_row: The 1-based sheet row, for pointing a human at it.
        source_tab: Exact read-only form tab that supplied the row.

    Returns:
        The submission, with the same tab-and-row provisional identity a poll
        gives it. Complete-snapshot reconciliation makes that identity durable
        before either polling or a manual start can open work.

    Raises:
        Nothing.
    """
    intake = from_row(row, columns)
    submitted_at = row[0].strip() if row else ""
    if not submitted_at:
        msg = f"row {sheet_row} has no submission timestamp, so I cannot identify it safely"
        raise SheetError(msg)
    clean_tab = source_tab.strip()
    return Submission(
        response_row_id=(
            source_identity(clean_tab, sheet_row)
            if clean_tab
            else legacy_identity(submitted_at, intake.agent_email, intake.address)
        ),
        sheet_row=sheet_row,
        submitted_at=submitted_at,
        intake=intake,
        content_hash=content_hash(row),
        source_tab=clean_tab,
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
    return parse_submissions(rows, tab)


def parse_submissions(rows: list[list[str]], tab: str) -> list[Submission]:
    """Parse one already-read, internally consistent responses-tab snapshot.

    Args:
        rows: The complete tab beginning at row one.
        tab: Exact read-only source tab name.

    Returns:
        Every nonblank response row in the snapshot.

    Raises:
        SheetError: If the snapshot has no recognisable header or a response
            has no timestamp.
    """
    header_index, columns = find_header(rows)
    out: list[Submission] = []
    # `start` is the 1-based sheet row of the first row after the header, so a
    # blank leading row cannot shift what Gable tells a human to look at.
    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue
        out.append(submission_from_row(row, columns, offset, source_tab=tab))
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
                submission.source_tab,
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
        _remember_source_rows(connection, submissions)
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


def new_submissions(
    connection: Connection,
    submissions: list[Submission],
    *,
    source_tab: str = "",
) -> list[Submission]:
    """The rows that still need building.

    Args:
        connection: An open database connection.
        submissions: Everything currently on the sheet.
        source_tab: Exact tab read for this complete snapshot. Required when
            the tab is empty so previously active source-row aliases retire.

    Returns:
        Submissions that have never had a run attempt. Paused work resumes only
        from its owned Slack thread, and failed work is not restarted by the
        poller. Empty until the backfill has been adopted — a poller that has
        not been told which rows are history must not guess.

    Raises:
        sqlite3.Error: on a query failure.
    """
    if not backfill_adopted(connection):
        return []
    pending: list[Submission] = []
    connection.execute("SAVEPOINT reconcile_current_submissions")
    try:
        reconciled = reconcile_submissions(connection, submissions)
        seen_ids: set[str] = set()
        for submission in reconciled:
            store.record_submission(
                connection,
                submission.response_row_id,
                submission.sheet_row,
                submission.submitted_at,
                submission.intake,
                submission.content_hash,
                submission.source_tab,
            )
            if submission.response_row_id in seen_ids:
                continue
            seen_ids.add(submission.response_row_id)
            if store.has_been_handled(connection, submission.response_row_id):
                continue
            pending.append(submission)
        snapshot_tabs = frozenset({source_tab.strip()} - {""})
        _remember_source_rows(connection, reconciled, snapshot_tabs)
        connection.execute("RELEASE reconcile_current_submissions")
    except Exception:
        connection.execute("ROLLBACK TO reconcile_current_submissions")
        connection.execute("RELEASE reconcile_current_submissions")
        raise
    return pending


def reconcile_identity(connection: Connection, submission: Submission) -> Submission:
    """Resolve only a source-free legacy row; source tabs require a snapshot.

    A single physical row cannot reveal whether earlier rows were inserted,
    deleted, or reordered. Runtime and operator entry points therefore call
    ``reconcile_submissions`` on the complete tab. This compatibility seam is
    retained only for historical source-free callers.
    """
    if submission.source_tab:
        raise SheetError("a sourced response must be reconciled with its complete tab snapshot")
    return reconcile_submissions(connection, [submission])[0]


def reconcile_submissions(
    connection: Connection,
    submissions: list[Submission],
) -> list[Submission]:
    """Resolve a complete tab before any provisional row id becomes work."""
    return _reconcile_submissions(connection, submissions)


def record_source_snapshot(
    connection: Connection,
    submissions: list[Submission],
    source_tab: str,
) -> list[Submission]:
    """Reconcile and persist inert metadata for one complete source snapshot.

    This opens no run and marks nothing handled. The manual row tool uses it so
    a later poll has the same full-tab identity evidence as the row selection
    that preceded it.
    """
    connection.execute("SAVEPOINT record_source_snapshot")
    try:
        reconciled = reconcile_submissions(connection, submissions)
        for item in reconciled:
            store.record_submission(
                connection,
                item.response_row_id,
                item.sheet_row,
                item.submitted_at,
                item.intake,
                item.content_hash,
                item.source_tab,
            )
        _remember_source_rows(connection, reconciled, frozenset({source_tab.strip()} - {""}))
        connection.execute("RELEASE record_source_snapshot")
    except Exception:
        connection.execute("ROLLBACK TO record_source_snapshot")
        connection.execute("RELEASE record_source_snapshot")
        raise
    return reconciled


def all_salespeople(connection: Connection) -> list[dict[str, str]]:
    """Every mirrored roster row, for matching an agent by name.

    The form's email field is whoever filled the form in. On 2026-08-19 one
    person submitted two requests for two other agents, so the address on the
    row identified the submitter and not the agent whose flyer it is. Matching
    by name needs the whole roster in hand; it is 39 rows, so this is a read
    rather than a query per candidate spelling.

    Args:
        connection: An open database connection.

    Returns:
        Every roster row as a dict, in filed order.

    Raises:
        sqlite3.Error: on a query failure.
    """
    rows = connection.execute(
        "SELECT email, first_name, last_name, phone, template, headshot_url, brokerage_url"
        " FROM salespeople ORDER BY rowid"
    ).fetchall()
    return [dict(row) for row in rows]


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
