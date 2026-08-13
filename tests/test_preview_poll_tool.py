"""Read-only poll preview reports unique unhandled identities."""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.sheets import repository as repo
from gable.sheets.identity import source_identity
from tests.runner_support import record, submission

pending_submissions = cast(
    Callable[[sqlite3.Connection, list[repo.Submission]], list[repo.Submission]],
    importlib.import_module("tools.preview_poll").pending_submissions,
)


def test_preview_reports_only_unhandled_reconciled_rows(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    base_handled = submission(address="1 Handled St")
    base_pending = submission(address="2 Pending St")
    handled = repo.Submission(
        source_identity("Form Responses 1", 2),
        2,
        base_handled.submitted_at,
        base_handled.intake,
        "handled-content",
        "Form Responses 1",
    )
    pending = repo.Submission(
        source_identity("Form Responses 1", 3),
        3,
        base_pending.submitted_at,
        base_pending.intake,
        "pending-content",
        "Form Responses 1",
    )
    record(connection, handled)
    run = store.start_run(connection, handled.response_row_id)
    store.set_status(connection, run.run_id, "skipped", "historical")

    assert pending_submissions(connection, [handled, pending]) == [pending]
