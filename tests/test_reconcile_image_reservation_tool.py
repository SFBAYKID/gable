"""The exceptional image-reservation reconciliation stays explicit and auditable."""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

from gable import spend
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake

main = cast(
    Callable[[list[str] | None], int],
    importlib.import_module("tools.reconcile_image_reservation").main,
)


def _reservation(path: Path) -> tuple[sqlite3.Connection, int, str]:
    """Create one listing-scoped image reservation for a tool test."""
    connection = connect(path)
    apply_migrations(connection)
    store.record_submission(
        connection,
        "response-tool",
        48,
        "today",
        Intake(
            agent_email="agent@example.com",
            agent_name="Agent Example",
            request_type="Sold",
            address="1 Main St",
            post_details="",
            open_house="",
            new_price="",
            closing_price="",
            extra_notes="",
            side="",
            notes="",
        ),
    )
    run_id = store.start_run(connection, "response-tool").run_id
    spend.record(
        connection,
        spend.Estimate(
            "openai",
            "gpt-image-2",
            spend.IMAGE_EDIT_RESERVE_USD,
            spend.IMAGE_UPSCALE_DETAIL,
        ),
        run_id,
    )
    spend_id = int(connection.execute("SELECT id FROM spend").fetchone()[0])
    return connection, spend_id, run_id


def _args(path: Path, spend_id: int) -> list[str]:
    """Build the required operator arguments without the commit switch."""
    return [
        "--db",
        str(path),
        "--spend-id",
        str(spend_id),
        "--reason",
        "invalid_request_dimensions",
        "--evidence",
        "HTTP 400; 1088x512 is below the documented 655360 pixels",
    ]


def test_preview_does_not_release_the_reservation(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    connection, spend_id, _run_id = _reservation(path)
    connection.close()

    assert main(_args(path, spend_id)) == 0

    checked = connect(path)
    assert checked.execute("SELECT COUNT(*) FROM operation_releases").fetchone()[0] == 0
    assert spend.total_spent(checked) == spend.IMAGE_EDIT_RESERVE_USD
    checked.close()


def test_commit_appends_one_release_without_changing_spend(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    connection, spend_id, run_id = _reservation(path)
    connection.close()

    assert main([*_args(path, spend_id), "--commit"]) == 0
    assert main([*_args(path, spend_id), "--commit"]) == 2

    checked = connect(path)
    release = checked.execute(
        "SELECT spend_id, run_id, reason, evidence FROM operation_releases"
    ).fetchone()
    assert tuple(release) == (
        spend_id,
        run_id,
        "invalid_request_dimensions",
        "HTTP 400; 1088x512 is below the documented 655360 pixels",
    )
    assert spend.total_spent(checked) == spend.IMAGE_EDIT_RESERVE_USD
    checked.close()


def test_commit_refuses_a_non_image_reservation(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    connection, spend_id, _run_id = _reservation(path)
    connection.execute("UPDATE spend SET note = 'conversation' WHERE id = ?", (spend_id,))
    connection.close()

    assert main([*_args(path, spend_id), "--commit"]) == 2

    checked = connect(path)
    assert checked.execute("SELECT COUNT(*) FROM operation_releases").fetchone()[0] == 0
    checked.close()


def test_tool_refuses_a_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    assert main(_args(missing, 46)) == 2
    assert not missing.exists()
