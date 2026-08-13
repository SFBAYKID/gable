"""Refreshing the exact read-only sources behind a paused Slack run."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.sheets import repository as sheet_repo
from gable.slackapp import runtime
from tests.runner_support import record, submission


class _Sheet:
    """The helper delegates parsing to the repository; this only identifies a client."""

    def read(self, _a1_range: str) -> list[list[str]]:
        raise AssertionError("repository reading is replaced in these focused tests")


def test_refresh_reloads_the_original_tab_and_updates_the_saved_form_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    original = replace(submission(rid="source-refresh"), source_tab="Testing_1")
    record(connection, original)
    run = store.start_run(connection, original.response_row_id)
    corrected = replace(
        original,
        intake=replace(original.intake, closing_price="625000"),
        content_hash="corrected-hash",
    )
    tabs: list[str] = []
    roster_refreshes: list[str] = []

    def refresh_roster(
        _drive: object,
        _connection: object,
        _drive_id: str,
        folder_id: str,
    ) -> int:
        roster_refreshes.append(folder_id)
        return 1

    monkeypatch.setattr(runtime, "sync_contacts", refresh_roster)

    def read_current(_client: object, tab: str) -> list[object]:
        tabs.append(tab)
        return [corrected]

    monkeypatch.setattr(sheet_repo, "read_submissions", read_current)

    refreshed = runtime.refresh_submission_sources(
        connection,
        run,
        _Sheet(),
        object(),
        "drive-1",
        "templates-1",
    )

    assert tabs == ["Testing_1"]
    assert roster_refreshes == ["templates-1"]
    assert refreshed.intake.closing_price == "625000"
    assert refreshed.content_hash == "corrected-hash"
    assert refreshed.source_tab == "Testing_1"


def test_refresh_refuses_to_guess_when_the_original_row_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    original = replace(submission(rid="source-missing"), source_tab="Testing_1")
    record(connection, original)
    run = store.start_run(connection, original.response_row_id)
    monkeypatch.setattr(runtime, "sync_contacts", lambda *_args: 1)
    monkeypatch.setattr(sheet_repo, "read_submissions", lambda *_args: [])

    with pytest.raises(runtime.SourceRefreshError, match="could not be identified once"):
        runtime.refresh_submission_sources(
            connection,
            run,
            _Sheet(),
            object(),
            "drive-1",
            "templates-1",
        )

    saved = store.load_submission(connection, original.response_row_id)
    assert saved is not None and saved.content_hash == original.content_hash


def test_legacy_row_without_tab_refreshes_roster_but_never_guesses_a_form_tab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    original = submission(rid="source-legacy")
    record(connection, original)
    run = store.start_run(connection, original.response_row_id)
    monkeypatch.setattr(runtime, "sync_contacts", lambda *_args: 1)
    monkeypatch.setattr(
        sheet_repo,
        "read_submissions",
        lambda *_args: pytest.fail("a tab-less historical row must not guess a source"),
    )

    refreshed = runtime.refresh_submission_sources(
        connection,
        run,
        _Sheet(),
        object(),
        "drive-1",
        "templates-1",
    )

    assert refreshed.source_tab == ""
    assert refreshed.intake == original.intake
