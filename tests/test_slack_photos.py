"""Tests for resuming a paused listing from a Slack thread upload."""

from __future__ import annotations

import io
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from gable import spend
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.photos.enhance import EnhancementError
from gable.pipeline.runner import RunResult
from gable.sheets import repository as repo
from gable.slackapp.photos import PhotoHandoff
from gable.slackapp.runtime import guarded_upscale_photo

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

    def resume(self, submission: repo.Submission, run_id: str) -> RunResult:
        """Record a same-run resume without rendering Google Slides."""
        self.seen.extend([submission.response_row_id, run_id])
        store.set_status(self.connection, run_id, "delivered", "test delivered")
        return RunResult(run_id=run_id, status="delivered")


def _handoff(
    path: Path,
    seen: list[str],
    image: bytes | None = None,
    upscale: Callable[[sqlite3.Connection, str, bytes, int, int], bytes] | None = None,
) -> PhotoHandoff:
    supplied = image if image is not None else _jpeg()

    def runner_for(connection: sqlite3.Connection, url: str, thread: str) -> FakeRunner:
        assert url == PUBLIC_URL
        assert thread == THREAD
        return FakeRunner(connection, seen)

    return PhotoHandoff(
        db_path=path,
        bot_token="test-token",
        allowed_channel=CHANNEL,
        target_width=1080,
        target_height=1350,
        public_root=path.parent / "photos",
        public_base="http://198.51.100.7",
        runner_for=runner_for,
        upscale=upscale,
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


def test_one_thread_image_resumes_the_same_run_without_a_new_attempt(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []

    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    assert said == "I resized and fitted the photo and finished the flyer."
    assert seen == ["response-1", run_id]
    connection = connect(path)
    assert store.run_attempt_count(connection, "response-1") == 1
    run = store.latest_run(connection, "response-1")
    assert run is not None and run.status == "delivered"
    photo = connection.execute(
        "SELECT photo_url, photo_source, ai_enhanced FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert tuple(photo) == (PUBLIC_URL, "carmen", 0)
    connection.close()


def test_an_upload_outside_the_configured_channel_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    _paused_database(path)
    client = FakeSlackClient()

    said = _handoff(path, []).handle(_event(channel="COTHER"), client)

    assert "Gable channel" in said
    assert client.calls == []


def test_a_top_level_upload_is_not_guessed_at(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    _paused_database(path)
    client = FakeSlackClient()

    said = _handoff(path, []).handle(_event(thread_ts=""), client)

    assert "inside the listing thread" in said
    assert client.calls == []


def test_multiple_images_are_not_silently_reduced_to_the_first(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    _paused_database(path)
    files = [{"id": "F1"}, {"id": "F2"}]

    said = _handoff(path, []).handle(_event(files=files), FakeSlackClient())

    assert "exactly one image" in said


def test_a_non_image_upload_leaves_the_run_paused(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    _paused_database(path)

    said = _handoff(path, []).handle(_event(), FakeSlackClient("application/pdf"))

    assert "not an image" in said
    connection = connect(path)
    run = store.latest_run(connection, "response-1")
    assert run is not None and run.status == "needs_photo"
    connection.close()


def test_a_tiny_upload_is_upscaled_automatically_and_resumes_the_run(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []
    calls: list[tuple[str, int, int]] = []

    def upscale(
        _connection: sqlite3.Connection,
        received_run_id: str,
        _image: bytes,
        width: int,
        height: int,
    ) -> bytes:
        calls.append((received_run_id, width, height))
        return _jpeg(width, height)

    said = _handoff(path, seen, _jpeg(200, 200), upscale).handle(_event(), FakeSlackClient())

    assert said == "I sharpened, enlarged, and fitted the photo and finished the flyer."
    assert calls == [(run_id, 1080, 1350)]
    assert seen == ["response-1", run_id]
    connection = connect(path)
    row = connection.execute("SELECT ai_enhanced FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row["ai_enhanced"] == 1
    connection.close()


def test_a_failed_ai_upscale_uses_the_original_without_asking_again(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []

    def fail(
        _connection: sqlite3.Connection,
        _run_id: str,
        _image: bytes,
        _width: int,
        _height: int,
    ) -> bytes:
        msg = "fixed test failure"
        raise RuntimeError(msg)

    said = _handoff(path, seen, _jpeg(200, 200), fail).handle(_event(), FakeSlackClient())

    assert said == "I resized and fitted the photo and finished the flyer."
    assert seen == ["response-1", run_id]
    connection = connect(path)
    row = connection.execute("SELECT ai_enhanced FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row["ai_enhanced"] == 0
    connection.close()


def test_a_listing_can_never_buy_a_second_upscale(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    calls: list[str] = []

    def provider(
        image: bytes,
        api_key: str,
        model: str,
        width: int,
        height: int,
    ) -> bytes:
        calls.append(f"{api_key}:{model}:{width}x{height}")
        return image

    first = guarded_upscale_photo(
        connection,
        run_id,
        _jpeg(200, 200),
        1080,
        1350,
        enabled=True,
        max_calls=1,
        api_key="test-key",
        model="gpt-image-2",
        provider=provider,
    )
    with pytest.raises(EnhancementError, match="already used"):
        guarded_upscale_photo(
            connection,
            run_id,
            _jpeg(200, 200),
            1080,
            1350,
            enabled=True,
            max_calls=1,
            api_key="test-key",
            model="gpt-image-2",
            provider=provider,
        )

    assert first
    assert calls == ["test-key:gpt-image-2:1080x1350"]
    assert spend.operation_count(connection, run_id, spend.IMAGE_UPSCALE_DETAIL) == 1
    connection.close()


def test_a_disabled_photo_policy_spends_nothing(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    called = False

    def provider(
        image: bytes,
        _api_key: str,
        _model: str,
        _width: int,
        _height: int,
    ) -> bytes:
        nonlocal called
        called = True
        return image

    with pytest.raises(EnhancementError, match="disabled by the photo policy"):
        guarded_upscale_photo(
            connection,
            run_id,
            _jpeg(200, 200),
            1080,
            1350,
            enabled=False,
            max_calls=1,
            api_key="",
            model="gpt-image-2",
            provider=provider,
        )

    assert called is False
    assert spend.operation_count(connection, run_id, spend.IMAGE_UPSCALE_DETAIL) == 0
    connection.close()


def test_a_public_host_failure_keeps_the_run_paused(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    _paused_database(path)
    handoff = _handoff(path, [])
    object.__setattr__(handoff, "verify", lambda _url: (False, "not found"))

    said = handoff.handle(_event(), FakeSlackClient())

    assert "could not fetch it" in said
    connection = connect(path)
    run = store.latest_run(connection, "response-1")
    assert run is not None and run.status == "needs_photo"
    connection.close()
