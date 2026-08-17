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
SCHEMA_VERSION: Final[int] = 13

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
    (
        7,
        """
        -- Reservations remain in the spend total even when a provider rejects
        -- a request before model execution. A human operator may append one
        -- narrowly evidenced release so that rejection does not consume the
        -- listing's one actual image-model operation forever.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_spend_id_run
            ON spend (id, run_id);

        CREATE TABLE IF NOT EXISTS operation_releases (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            spend_id          INTEGER NOT NULL UNIQUE,
            run_id            TEXT NOT NULL REFERENCES runs(run_id),
            operation_detail  TEXT NOT NULL CHECK (
                operation_detail = 'conservative real-photo upscale reservation'
            ),
            reason            TEXT NOT NULL CHECK (length(trim(reason)) > 0),
            evidence          TEXT NOT NULL CHECK (length(trim(evidence)) >= 20),
            at                TEXT NOT NULL,
            FOREIGN KEY (spend_id, run_id) REFERENCES spend(id, run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_operation_releases_run
            ON operation_releases (run_id, operation_detail);
        """,
    ),
    (
        8,
        """
        -- A run outcome and its Slack delivery are separate durable writes.
        -- Keep the exact message here before posting so a restart can retry it
        -- without rebuilding the flyer or pretending the person saw it. The
        -- same table carries questions and truthful terminal/review outcomes;
        -- stable client ids make every retry the same Slack message.
        CREATE TABLE IF NOT EXISTS run_questions (
            question_id        TEXT PRIMARY KEY,
            run_id             TEXT NOT NULL REFERENCES runs(run_id),
            notification_kind  TEXT NOT NULL DEFAULT 'question'
                CHECK (notification_kind IN ('question', 'outcome', 'action')),
            pending_status     TEXT NOT NULL DEFAULT 'needs_review',
            target_status      TEXT NOT NULL,
            message            TEXT NOT NULL,
            question_label     TEXT NOT NULL,
            headline           TEXT NOT NULL DEFAULT '',
            thread_ts          TEXT NOT NULL DEFAULT '',
            headline_ts        TEXT NOT NULL DEFAULT '',
            question_ts        TEXT NOT NULL DEFAULT '',
            headline_client_id TEXT NOT NULL UNIQUE,
            question_client_id TEXT NOT NULL UNIQUE,
            confirmed_reason   TEXT NOT NULL DEFAULT '',
            confirmation_detail TEXT NOT NULL DEFAULT '',
            created_at         TEXT NOT NULL,
            confirmed_at       TEXT NOT NULL DEFAULT '',
            -- A person can satisfy the exact pending request before the
            -- posting worker records Slack's acknowledgement.  Keep that
            -- distinct from confirmation: no audit row may claim Slack
            -- confirmed a question merely because its requested photo arrived.
            satisfied_at       TEXT NOT NULL DEFAULT '',
            satisfaction_detail TEXT NOT NULL DEFAULT ''
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_run_questions_pending
            ON run_questions (run_id)
            WHERE confirmed_at = '' AND satisfied_at = '';
        CREATE INDEX IF NOT EXISTS idx_run_questions_run
            ON run_questions (run_id, created_at);
        """,
    ),
    (
        9,
        """
        -- Legacy tuple ids could collapse byte-identical Google Form rows.
        -- Preserve multiplicity from each complete tab snapshot so a later
        -- identical response is new work while historical copies do not replay.
        CREATE TABLE IF NOT EXISTS submission_source_rows (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            response_row_id   TEXT NOT NULL REFERENCES submissions(response_row_id),
            source_tab        TEXT NOT NULL,
            sheet_row         INTEGER NOT NULL,
            submitted_at      TEXT NOT NULL,
            content_hash      TEXT NOT NULL,
            active            INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            first_seen_at     TEXT NOT NULL,
            last_seen_at      TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_submission_source_rows_active
            ON submission_source_rows (
                source_tab, response_row_id, content_hash, active, sheet_row
            );
        """,
    ),
    (
        10,
        """
        -- External Slack writes must have one durable owner, including when
        -- two processes overlap during a restart. Attempt markers deliberately
        -- prefer an honest pending row over a second write after an ambiguous
        -- acknowledgement; later workers reconcile the stable client id.
        ALTER TABLE run_questions
            ADD COLUMN delivery_claim_token TEXT NOT NULL DEFAULT '';
        ALTER TABLE run_questions
            ADD COLUMN delivery_claimed_at TEXT NOT NULL DEFAULT '';
        ALTER TABLE run_questions
            ADD COLUMN headline_attempted_at TEXT NOT NULL DEFAULT '';
        ALTER TABLE run_questions
            ADD COLUMN headline_attempt_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE run_questions
            ADD COLUMN question_attempted_at TEXT NOT NULL DEFAULT '';
        ALTER TABLE run_questions
            ADD COLUMN question_attempt_count INTEGER NOT NULL DEFAULT 0;

        -- The final photo claim records the Slack event identity with the run.
        -- Preparation before that point is content-addressed and safe to redo;
        -- after it, restart recovery owns the visible active run.
        ALTER TABLE runs
            ADD COLUMN photo_event_id TEXT NOT NULL DEFAULT '';

        ALTER TABLE template_audits
            ADD COLUMN delivery_claim_token TEXT NOT NULL DEFAULT '';
        ALTER TABLE template_audits
            ADD COLUMN delivery_claimed_at TEXT NOT NULL DEFAULT '';
        ALTER TABLE template_audits
            ADD COLUMN notification_attempted_at TEXT NOT NULL DEFAULT '';
        ALTER TABLE template_audits
            ADD COLUMN notification_attempt_count INTEGER NOT NULL DEFAULT 0;

        -- Process memory cannot suppress the same Slack event after restart.
        -- Claim a user action before external work; an abandoned claim remains
        -- visible for operator repair instead of repeating an unknown mutation
        -- or paid inspection behind the user's back.
        CREATE TABLE IF NOT EXISTS slack_event_claims (
            route          TEXT NOT NULL,
            event_id       TEXT NOT NULL,
            subject_id     TEXT NOT NULL,
            thread_ts      TEXT NOT NULL,
            fingerprint    TEXT NOT NULL,
            claimed_at     TEXT NOT NULL,
            completed_at   TEXT NOT NULL DEFAULT '',
            detail         TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (route, event_id)
        );

        CREATE INDEX IF NOT EXISTS idx_slack_event_claims_subject
            ON slack_event_claims (subject_id, claimed_at);
        """,
    ),
    (
        11,
        """
        -- A person answering the question Gable asked is the best evidence
        -- there is, and until now it had nowhere to live. `research_gate`
        -- starts from an empty `known` and trusts only a freshly proven web
        -- result, so Chase replying "List price is $200,000" was acknowledged
        -- and then discarded: the run stayed at needs_info and the value never
        -- reached property_facts.
        --
        -- This is a separate table rather than a row in property_facts because
        -- the provenance is different in kind. property_facts holds one row per
        -- address with one source_url, so a later scrape would silently
        -- overwrite what a human stated, and a human value would erase the
        -- scrape's audit URL. Kept apart, a stated fact always outranks a
        -- looked-up one and neither destroys the other.
        CREATE TABLE IF NOT EXISTS supplied_facts (
            address_key TEXT NOT NULL,
            field       TEXT NOT NULL,
            value       TEXT NOT NULL,
            supplied_by TEXT NOT NULL DEFAULT '',
            supplied_at TEXT NOT NULL,
            PRIMARY KEY (address_key, field)
        );
        """,
    ),
    (
        12,
        """
        -- The address a person gave when Gable could not read the one on the
        -- form. Row 81 arrived as "1011 Winged Foot Drive" with no city, state
        -- or ZIP, Gable asked what the address was, Chase answered it exactly,
        -- and nothing happened: there was no way to accept the answer, so the
        -- run sat at needs_info and Carmen would have waited forever.
        --
        -- It cannot live in supplied_facts, which is keyed by address_key —
        -- the whole problem is that there is no usable address to key on. And
        -- it cannot be written back to the form, which Gable never modifies.
        -- So it belongs to the submission, and `load_submission` lays it over
        -- the form's own value: re-reading the sheet cannot lose the correction
        -- and the response tab stays untouched.
        CREATE TABLE IF NOT EXISTS stated_addresses (
            response_row_id TEXT PRIMARY KEY,
            address         TEXT NOT NULL,
            stated_by       TEXT NOT NULL DEFAULT '',
            stated_at       TEXT NOT NULL
        );
        """,
    ),
    (
        13,
        """
        -- The form's social-media content type, which decides whether a
        -- graphic is wanted at all: a Reel or a Story is video or animation and
        -- Gable builds nothing for it. Stored because a submission reloaded
        -- from here must equal the one that was written — a resumed thread that
        -- silently lost this field would read every stored row as "build".
        --
        -- Defaults to empty, which means "build". Rows recorded before this
        -- migration therefore keep the behaviour they already had.
        ALTER TABLE submissions ADD COLUMN content_type TEXT NOT NULL DEFAULT '';
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
