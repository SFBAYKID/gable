"""Shared fixtures for the Slack photo handoff and its durability tests.

These build a paused listing run, a Slack client that returns one image without
a network call, and a runner that claims the same run instead of rendering
Google Slides. Nothing here touches a real API.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from typing import Any

from PIL import Image

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.pipeline.runner import RunResult
from gable.sheets import repository as repo
from gable.slackapp.photos import PhotoHandoff

CHANNEL = "C0BP597644B"
THREAD = "1723000000.100"
PUBLIC_URL = "http://198.51.100.7/0123456789abcdef.jpg"


def _jpeg(width: int = 2160, height: int = 2700) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (120, 160, 200)).save(out, format="JPEG")
    return out.getvalue()


def _intake() -> Intake:
    return Intake(
        agent_email="chase@monarchconnected.com",
        agent_name="Chase Gonzales",
        request_type="New Listing",
        address="123 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )


def _paused_database(path: Path) -> str:
    connection = connect(path)
    apply_migrations(connection)
    intake = _intake()
    assert store.record_submission(connection, "response-1", 100, "today", intake, "hash")
    run = store.start_run(connection, "response-1")
    store.set_status(
        connection,
        run.run_id,
        "needs_photo",
        "waiting on Carmen",
        slack_thread_ts=THREAD,
    )
    connection.close()
    return run.run_id


class FakeSlackClient:
    """Returns one image file without a network call."""

    def __init__(self, mime_type: str = "image/jpeg") -> None:
        """Set the reported MIME type."""
        self.mime_type = mime_type
        self.calls: list[str] = []

    def files_info(self, *, file: str) -> dict[str, Any]:
        """Return private metadata for the requested file."""
        self.calls.append(file)
        return {
            "file": {
                "mimetype": self.mime_type,
                "url_private_download": "https://files.slack.com/files-pri/photo.jpg",
            }
        }


class FakeRunner:
    """Marks the same run delivered and records what it received."""

    def __init__(self, connection: sqlite3.Connection, seen: list[str]) -> None:
        """Bind the database and shared recorder."""
        self.connection = connection
        self.seen = seen

    def resume(
        self,
        submission: repo.Submission,
        run_id: str,
        *,
        resume_fields: dict[str, str | int] | None = None,
        expected_status: str | None = None,
    ) -> RunResult:
        """Record a same-run resume without rendering Google Slides."""
        self.seen.extend([submission.response_row_id, run_id])
        assert expected_status == "needs_photo"
        fields = resume_fields or {}
        claimed = store.claim_run_for_photo(
            self.connection,
            run_id,
            THREAD,
            fields,
        )
        assert claimed
        store.set_status(self.connection, run_id, "delivered", "test delivered")
        return RunResult(
            run_id=run_id,
            status="delivered",
            said=["Your flyer is ready. <https://slides.test/x|Open the flyer>"],
        )


def _handoff(
    path: Path,
    seen: list[str],
    image: bytes | None = None,
) -> PhotoHandoff:
    supplied = image if image is not None else _jpeg()

    def runner_for(
        connection: sqlite3.Connection,
        url: str,
        thread: str,
        _progress: object = None,
    ) -> FakeRunner:
        assert url == PUBLIC_URL
        assert thread == THREAD
        return FakeRunner(connection, seen)

    return PhotoHandoff(
        db_path=path,
        bot_token="test-token",
        allowed_channel=CHANNEL,
        max_edge_px=2400,
        jpeg_quality=85,
        public_root=path.parent / "photos",
        public_base="http://198.51.100.7",
        runner_for=runner_for,
        download=lambda _url, _token, _limit: supplied,
        publish=lambda _root, _base, fitted: PUBLIC_URL if fitted else "",
        verify=lambda _url: (True, "image/jpeg"),
    )


def _event(**overrides: object) -> dict[str, Any]:
    event: dict[str, Any] = {
        "channel": CHANNEL,
        "thread_ts": THREAD,
        "subtype": "file_share",
        "files": [{"id": "F123"}],
    }
    event.update(overrides)
    return event
