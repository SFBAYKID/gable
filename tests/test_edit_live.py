"""Bounded Drive copy recovery for post-delivery flyer edits."""

from __future__ import annotations

from typing import Any

import pytest

from gable.pipeline.edit_live import DraftCopyError, copy_edit_draft, find_edit_drafts


class FakeDrive:
    """Return scripted Drive pages and copy outcomes through discovery chaining."""

    def __init__(
        self,
        searches: list[dict[str, Any]],
        *,
        copy_result: dict[str, Any] | Exception | None = None,
    ) -> None:
        """Store ordered search pages and the one possible copy response."""
        self.searches = searches
        self.copy_result = copy_result or {
            "id": "draft-1",
            "webViewLink": "https://docs.example/draft-1",
        }
        self.operation = ""
        self.copy_calls: list[dict[str, Any]] = []

    def files(self) -> FakeDrive:
        """Match the generated Drive resource chain."""
        return self

    def list(self, **kwargs: Any) -> FakeDrive:  # noqa: ANN401
        """Select a private-property search."""
        self.operation = "list"
        assert kwargs["corpora"] == "drive"
        assert kwargs["supportsAllDrives"] is True
        assert "gable_edit_id" in kwargs["q"]
        return self

    def get(self, **kwargs: Any) -> FakeDrive:  # noqa: ANN401
        """Select the source metadata read."""
        self.operation = "get"
        assert kwargs["fileId"] == "source-1"
        return self

    def copy(self, **kwargs: Any) -> FakeDrive:  # noqa: ANN401
        """Record the sole allowed copy attempt."""
        self.operation = "copy"
        self.copy_calls.append(kwargs)
        return self

    def execute(self) -> dict[str, Any]:
        """Return the response for the selected operation."""
        if self.operation == "list":
            return self.searches.pop(0)
        if self.operation == "get":
            return {"id": "source-1", "name": "Original flyer"}
        if isinstance(self.copy_result, Exception):
            raise self.copy_result
        return self.copy_result


def test_a_new_edit_copy_has_stable_private_identity_and_separate_parent() -> None:
    drive = FakeDrive([{"files": [], "nextPageToken": ""}])

    file_id, url = copy_edit_draft(
        drive,
        "drive-1",
        "output-folder",
        "source-1",
        "edit-12345678",
    )

    assert (file_id, url) == ("draft-1", "https://docs.example/draft-1")
    assert len(drive.copy_calls) == 1
    body = drive.copy_calls[0]["body"]
    assert body["parents"] == ["output-folder"]
    assert body["appProperties"] == {
        "gable_edit_id": "edit-12345678",
        "gable_source_id": "source-1",
    }
    assert body["name"] == "Original flyer — Updated 12345678"


def test_a_lost_copy_acknowledgement_is_reconciled_without_second_write() -> None:
    drive = FakeDrive(
        [
            {"files": [], "nextPageToken": ""},
            {
                "files": [
                    {
                        "id": "accepted-draft",
                        "webViewLink": "https://docs.example/accepted-draft",
                    }
                ],
                "nextPageToken": "",
            },
        ],
        copy_result=TimeoutError("lost acknowledgement"),
    )

    assert copy_edit_draft(
        drive,
        "drive-1",
        "output-folder",
        "source-1",
        "edit-1",
    ) == ("accepted-draft", "https://docs.example/accepted-draft")
    assert len(drive.copy_calls) == 1


def test_an_unconfirmed_copy_is_never_blindly_retried() -> None:
    drive = FakeDrive(
        [
            {"files": [], "nextPageToken": ""},
            {"files": [], "nextPageToken": ""},
        ],
        copy_result=TimeoutError("lost acknowledgement"),
    )

    with pytest.raises(DraftCopyError, match="did not confirm"):
        copy_edit_draft(
            drive,
            "drive-1",
            "output-folder",
            "source-1",
            "edit-1",
        )

    assert len(drive.copy_calls) == 1


def test_multiple_marked_drafts_fail_closed_before_copying() -> None:
    drive = FakeDrive(
        [
            {
                "files": [
                    {"id": "one", "webViewLink": "https://docs.example/one"},
                    {"id": "two", "webViewLink": "https://docs.example/two"},
                ],
                "nextPageToken": "",
            }
        ]
    )

    with pytest.raises(DraftCopyError, match="multiple"):
        copy_edit_draft(
            drive,
            "drive-1",
            "output-folder",
            "source-1",
            "edit-1",
        )

    assert drive.copy_calls == []


def test_repeated_drive_cursor_is_unknown_not_a_partial_match() -> None:
    drive = FakeDrive(
        [
            {"files": [], "nextPageToken": "again"},
            {"files": [], "nextPageToken": "again"},
        ]
    )

    with pytest.raises(DraftCopyError, match="repeated"):
        find_edit_drafts(drive, "drive-1", "output-folder", "edit-1", "source-1")
