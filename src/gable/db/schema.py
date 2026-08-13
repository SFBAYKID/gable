"""The database Gable actually needs, and why it is SQLite.

**Why a database at all.** The Sheet is a good intake form and a poor datastore:
it has no types, no constraints, no transactions, and no way to ask "have I
already built this one?" without reading every row. Everything Gable *derives* —
what it looked up, what it asked, what it rendered, what a run cost — belongs
somewhere that can answer a question. The Sheet stays the source of truth for
what agents submitted; it is never written back to (CLAUDE.md §11).

**Why SQLite rather than Postgres.** At a hundred submissions a month on a 1 GB
droplet, a database server is a second thing to run, back up, patch and monitor,
in exchange for concurrency this workload does not have. SQLite is a file, it is
in the standard library, and `sqlite3` in WAL mode handles one writer and several
readers comfortably. If volume ever justifies Postgres the schema below ports
without drama — it uses no SQLite-specific types.

**What is deliberately not here.** No ORM. The queries are short and the schema
is small; an ORM would add a dependency and a layer of indirection to save a few
lines of SQL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

#: Bumped whenever a migration is added. `apply_migrations` uses it to decide
#: what still needs running.
SCHEMA_VERSION: Final[int] = 6

#: Each migration is (version, sql). They run in order and only once. Never edit
#: one that has shipped — add another, the same rule as the decision log.
MIGRATIONS: Final[tuple[tuple[int, str], ...]] = (
    (
        1,
        """
        -- One row per form submission Gable has seen. `response_row_id` is the
        -- deployed hash of timestamp, agent email and address. Polling
        -- reconciles corrected tuple fields by the immutable form timestamp;
        -- sheet row numbers are never identities.
        CREATE TABLE IF NOT EXISTS submissions (
            response_row_id   TEXT PRIMARY KEY,
            sheet_row         INTEGER NOT NULL,
            submitted_at      TEXT    NOT NULL,
            agent_email       TEXT    NOT NULL,
            agent_name        TEXT    NOT NULL,
            request_type      TEXT    NOT NULL,
            address           TEXT    NOT NULL,
            post_details      TEXT    NOT NULL DEFAULT '',
            open_house        TEXT    NOT NULL DEFAULT '',
            new_price         TEXT    NOT NULL DEFAULT '',
            closing_price     TEXT    NOT NULL DEFAULT '',
            extra_notes       TEXT    NOT NULL DEFAULT '',
            side              TEXT    NOT NULL DEFAULT '',
            notes             TEXT    NOT NULL DEFAULT '',
            first_seen_at     TEXT    NOT NULL,
            -- Row content as read, so a later edit to the submission is
            -- detectable even though the identity hash did not change.
            content_hash      TEXT    NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_submissions_agent
            ON submissions (agent_email);
        CREATE INDEX IF NOT EXISTS idx_submissions_row
            ON submissions (sheet_row);

        -- One row per attempt to turn a submission into a post. A submission
        -- can have several runs: a retry, or a rebuild after Carmen asks for
        -- one. The latest non-terminal run is the live one.
        CREATE TABLE IF NOT EXISTS runs (
            run_id            TEXT PRIMARY KEY,
            response_row_id   TEXT NOT NULL REFERENCES submissions(response_row_id),
            status            TEXT NOT NULL,
            template_file_id  TEXT NOT NULL DEFAULT '',
            template_label    TEXT NOT NULL DEFAULT '',
            output_file_id    TEXT NOT NULL DEFAULT '',
            output_url        TEXT NOT NULL DEFAULT '',
            photo_url         TEXT NOT NULL DEFAULT '',
            photo_source      TEXT NOT NULL DEFAULT '',
            ai_generated      INTEGER NOT NULL DEFAULT 0,
            slack_thread_ts   TEXT NOT NULL DEFAULT '',
            failure_reason    TEXT NOT NULL DEFAULT '',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_runs_submission
            ON runs (response_row_id);
        CREATE INDEX IF NOT EXISTS idx_runs_status
            ON runs (status);
        CREATE INDEX IF NOT EXISTS idx_runs_thread
            ON runs (slack_thread_ts);

        -- Every state change, append-only. AGENTS.md 6 requires that a
        -- listing's state be explainable from the log; this is that log.
        CREATE TABLE IF NOT EXISTS run_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL REFERENCES runs(run_id),
            at          TEXT NOT NULL,
            status      TEXT NOT NULL,
            detail      TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_events_run ON run_events (run_id, at);

        -- Facts Gable looked up rather than was told, keyed by address. Cached
        -- because the same property comes back for a listing, an open house and
        -- a sale, and paying to research it three times is waste.
        CREATE TABLE IF NOT EXISTS property_facts (
            address_key   TEXT PRIMARY KEY,
            address       TEXT NOT NULL,
            beds          TEXT NOT NULL DEFAULT '',
            baths         TEXT NOT NULL DEFAULT '',
            square_feet   TEXT NOT NULL DEFAULT '',
            list_price    TEXT NOT NULL DEFAULT '',
            year_built    TEXT NOT NULL DEFAULT '',
            source_url    TEXT NOT NULL DEFAULT '',
            confidence    REAL NOT NULL DEFAULT 0.0,
            looked_up_at  TEXT NOT NULL
        );

        -- The agent roster, mirrored from the Drive contact workbook so a
        -- listing lookup does not need another network call.
        CREATE TABLE IF NOT EXISTS salespeople (
            email          TEXT PRIMARY KEY,
            first_name     TEXT NOT NULL DEFAULT '',
            last_name      TEXT NOT NULL DEFAULT '',
            phone          TEXT NOT NULL DEFAULT '',
            template       TEXT NOT NULL DEFAULT '',
            headshot_url   TEXT NOT NULL DEFAULT '',
            brokerage_url  TEXT NOT NULL DEFAULT '',
            synced_at      TEXT NOT NULL
        );

        -- What each paid call cost, so a bill can be reconstructed
        -- (AGENTS.md 7). Written even on failure: a call that errored after
        -- the tokens were spent still cost money.
        CREATE TABLE IF NOT EXISTS spend (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id     TEXT NOT NULL DEFAULT '',
            at         TEXT NOT NULL,
            service    TEXT NOT NULL,
            model      TEXT NOT NULL DEFAULT '',
            units      REAL NOT NULL DEFAULT 0,
            unit_kind  TEXT NOT NULL DEFAULT '',
            note       TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_spend_run ON spend (run_id);
        """,
    ),
    (
        2,
        """
        -- A real photo enlarged by an image model is not synthetic, but the
        -- distinction must survive the Slack handoff and later audit.
        ALTER TABLE runs ADD COLUMN ai_enhanced INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        3,
        """
        -- Source-template checks are separate from listing runs. The first
        -- catalogue scan adopts existing files silently; later file ids are
        -- new uploads and receive one measured Slack review.
        CREATE TABLE IF NOT EXISTS template_audits (
            file_id          TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            modified_time    TEXT NOT NULL DEFAULT '',
            status           TEXT NOT NULL,
            summary          TEXT NOT NULL DEFAULT '',
            slack_thread_ts  TEXT NOT NULL DEFAULT '',
            checked_at       TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_template_audits_thread
            ON template_audits (slack_thread_ts);

        CREATE TABLE IF NOT EXISTS template_scan_state (
            singleton   INTEGER PRIMARY KEY CHECK (singleton = 1),
            adopted_at  TEXT NOT NULL
        );
        """,
    ),
    (
        4,
        """
        -- A source audit and its Slack delivery are separate external writes.
        -- Persist this bit before posting so a Slack failure retries only the
        -- stored verdict, never the paid visual inspection.
        ALTER TABLE template_audits
            ADD COLUMN notification_pending INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        5,
        """
        -- A human may approve one measured, non-structural warning before a
        -- later photo upload resumes the run. Persist exact warning codes on
        -- the run so an address-width approval cannot silently approve a new
        -- crop warning discovered in a later stage.
        ALTER TABLE runs
            ADD COLUMN approved_warning_codes TEXT NOT NULL DEFAULT '';
        ALTER TABLE runs
            ADD COLUMN pending_warning_code TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        6,
        """
        -- A paused Slack run must refresh the exact form tab it came from.
        -- Row numbers alone are ambiguous because production and Testing_1
        -- both have a row 48, and the form itself remains read-only.
        ALTER TABLE submissions
            ADD COLUMN source_tab TEXT NOT NULL DEFAULT '';
        """,
    ),
)


def _statements(sql: str) -> list[str]:
    """Split a migration into individual statements.

    Comments are stripped **before** splitting, not after. A comment in this
    very file contained a semicolon, which cut the statement it belonged to in
    half and left the fragment "this is that log." to be executed as SQL. The
    ordering is the whole fix.

    Args:
        sql: One migration's SQL, comments and all.

    Returns:
        Executable statements.

    Raises:
        Nothing.

    Note:
        Splitting on semicolons is correct for this schema because no statement
        contains one inside a string literal or a trigger body. Adding a trigger
        means replacing this with a real parser, and the migration that adds one
        should say so.
    """
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    return [chunk.strip() for chunk in without_comments.split(";") if chunk.strip()]


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database with the settings this workload wants.

    Args:
        path: Where the file lives. Parent directories are created.

    Returns:
        A connection with WAL enabled, foreign keys enforced, and rows returned
        as `sqlite3.Row` so callers index by column name.

    Raises:
        sqlite3.Error: if the file cannot be opened.
    """
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        file,
        timeout=30.0,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    # WAL lets the poller write while a Slack handler uses its own connection.
    # check_same_thread=False is a final guard against an accidental handoff,
    # but production still creates one connection per owning thread rather than
    # concurrently sharing a connection object.
    connection.execute("PRAGMA journal_mode=WAL")
    # Off by default in SQLite, which makes the REFERENCES clauses above
    # decorative rather than enforced.
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def current_version(connection: sqlite3.Connection) -> int:
    """The schema version this database is at.

    Args:
        connection: An open connection.

    Returns:
        0 for a database that has never been migrated.

    Raises:
        sqlite3.Error: on a query failure.
    """
    connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Bring the database up to `SCHEMA_VERSION`.

    Safe to call on every start: migrations already applied are skipped, so this
    is the boot path rather than a separate command someone has to remember.

    Args:
        connection: An open connection.

    Returns:
        How many migrations ran. Zero means it was already current.

    Raises:
        sqlite3.Error: if a migration fails. It fails inside a transaction, so a
            half-applied schema is not a state this can leave behind.
    """
    if not MIGRATIONS or MIGRATIONS[-1][0] != SCHEMA_VERSION:
        msg = "SCHEMA_VERSION must equal the newest declared migration"
        raise RuntimeError(msg)
    applied = 0
    at = current_version(connection)
    if at > SCHEMA_VERSION:
        msg = (
            f"database schema version {at} is newer than this code supports "
            f"({SCHEMA_VERSION}); refusing to run an older binary"
        )
        raise RuntimeError(msg)
    for version, sql in MIGRATIONS:
        if version <= at:
            continue
        # NOT executescript: it issues an implicit COMMIT before running, which
        # ends the transaction opened above and leaves the version row outside
        # it. Statements go in one at a time so a migration is genuinely atomic.
        connection.execute("BEGIN")
        try:
            for statement in _statements(sql):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        applied += 1
    return applied
