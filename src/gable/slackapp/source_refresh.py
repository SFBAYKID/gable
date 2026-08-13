"""Refresh the exact read-only sources behind a paused Slack run."""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from gable.agents.contacts import sync_contacts
from gable.db import store
from gable.sheets import repository as repo
from gable.sheets.client import ReadsRanges


class SourceRefreshError(RuntimeError):
    """The authoritative roster or original form row could not be refreshed."""


def refresh_submission_sources(
    connection: Connection,
    run: store.RunRow,
    sheet_client: ReadsRanges,
    drive: Any,  # noqa: ANN401 - googleapiclient resource
    drive_id: str,
    templates_folder_id: str,
) -> store.StoredSubmission:
    """Reload contacts and the exact source-tab row without guessing identity."""
    try:
        sync_contacts(drive, connection, drive_id, templates_folder_id)
        stored = store.load_submission(connection, run.response_row_id)
        if stored is None:
            raise SourceRefreshError("the saved request no longer exists")
        if not stored.source_tab:
            return stored
        current_tab = repo.record_source_snapshot(
            connection,
            repo.read_submissions(sheet_client, stored.source_tab),
            stored.source_tab,
        )
        matches = [item for item in current_tab if item.response_row_id == run.response_row_id]
        if len(matches) > 1 and len({item.content_hash for item in matches}) == 1:
            matches = [min(matches, key=lambda item: abs(item.sheet_row - stored.sheet_row))]
        if len(matches) != 1:
            raise SourceRefreshError("the original form response could not be identified once")
        refreshed = store.load_submission(connection, run.response_row_id)
        if refreshed is None:
            raise SourceRefreshError("the refreshed request could not be saved")
        return refreshed
    except SourceRefreshError:
        raise
    except Exception as exc:
        raise SourceRefreshError("the request sources could not be refreshed") from exc
