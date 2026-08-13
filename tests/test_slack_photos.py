"""Tests for resuming a paused listing from a Slack thread upload."""

from __future__ import annotations

import io
import sqlite3
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.photos.store import PublishError
from gable.pipeline.runner import RunResult
from gable.sheets import repository as repo
from gable.slackapp.brain import Decision
from gable.slackapp.editing import SlideEditor
from gable.slackapp.photos import (
    PhotoHandoff,
    PhotoHandoffError,
    _SlackOnlyRedirectHandler,
)

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
    ) -> RunResult:
        """Record a same-run resume without rendering Google Slides."""
        self.seen.extend([submission.response_row_id, run_id])
        fields = resume_fields or {}
        claimed = store.claim_paused_run(self.connection, run_id, fields)
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


def test_a_slack_download_redirect_cannot_leak_the_bot_token_off_slack() -> None:
    request = urllib.request.Request(
        "https://files.slack.com/files-pri/photo.jpg",
        headers={"Authorization": "Bearer test-token"},
    )

    with pytest.raises(PhotoHandoffError, match="outside its file service"):
        _SlackOnlyRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://attacker.example/capture",
        )


def test_a_slack_download_may_redirect_only_to_another_slack_host() -> None:
    request = urllib.request.Request(
        "https://files.slack.com/files-pri/photo.jpg",
        headers={"Authorization": "Bearer test-token"},
    )

    redirected = _SlackOnlyRedirectHandler().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://files.slack-edge.com/files-pri/photo.jpg",
    )

    assert redirected is not None
    assert redirected.full_url == "https://files.slack-edge.com/files-pri/photo.jpg"


def test_one_thread_image_resumes_the_same_run_without_a_new_attempt(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []

    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    # Nothing, on purpose: the run posted its own outcome and its link, so a
    # line here would be a fourth message restating the thread.
    assert said == ""
    assert seen == ["response-1", run_id]
    connection = connect(path)
    assert store.run_attempt_count(connection, "response-1") == 1
    run = store.latest_run(connection, "response-1")
    assert run is not None and run.status == "delivered"
    photo = connection.execute(
        "SELECT photo_url, photo_source, ai_enhanced FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert tuple(photo) == (PUBLIC_URL, "slack_upload", 0)
    connection.close()


def test_a_replacement_upload_resumes_the_delivered_run_without_overwriting_first(
    tmp_path: Path,
) -> None:
    """A confirmed replacement waits; the next upload claims that same run once."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(
        connection,
        run_id,
        "delivered",
        "first flyer delivered",
        output_file_id="existing-deck",
        output_url="https://docs.example/existing-deck",
        photo_url="http://images.example/first-house.jpg",
    )
    asked = SlideEditor(connection, object()).execute(
        Decision(
            reply="Send me the new property photo.",
            tool="replace_photo",
            arguments={"which": "hero"},
        ),
        THREAD,
    )
    waiting = store.run_by_id(connection, run_id)
    assert waiting is not None
    assert asked == "Send me the new property photo."
    assert waiting.status == "needs_photo"
    assert waiting.output_file_id == "existing-deck"
    assert waiting.photo_url == "http://images.example/first-house.jpg"
    connection.close()

    seen: list[str] = []
    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    assert said == ""
    assert seen == ["response-1", run_id]
    connection = connect(path)
    current = store.run_by_id(connection, run_id)
    assert current is not None
    assert current.status == "delivered"
    assert current.photo_url == PUBLIC_URL
    assert store.run_attempt_count(connection, "response-1") == 1
    connection.close()


def test_a_wrong_house_rejection_accepts_the_next_image_on_the_same_run(
    tmp_path: Path,
) -> None:
    """The visual gate's photo remedy must not strand a needs_photo run."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(
        connection,
        run_id,
        "needs_photo",
        "replacement supplied photo required after the house number contradicted the address",
        output_file_id="rejected-deck",
        output_url="https://docs.example/rejected-deck",
        photo_url="http://images.example/wrong-house.jpg",
        photo_source="slack_upload",
    )
    rejected = store.run_by_id(connection, run_id)
    assert rejected is not None
    assert rejected.output_file_id == "rejected-deck"
    assert rejected.photo_url == "http://images.example/wrong-house.jpg"
    connection.close()

    seen: list[str] = []
    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    assert said == ""
    assert seen == ["response-1", run_id]
    connection = connect(path)
    current = store.run_by_id(connection, run_id)
    assert current is not None
    assert current.status == "delivered"
    assert current.photo_url == PUBLIC_URL
    assert current.photo_source == "slack_upload"
    assert current.output_file_id == "rejected-deck"
    assert store.run_attempt_count(connection, "response-1") == 1
    event_statuses = [
        str(row["status"])
        for row in connection.execute(
            "SELECT status FROM run_events WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    ]
    assert event_statuses[-2:] == ["pending", "delivered"]
    connection.close()


def test_two_simultaneous_thread_uploads_do_not_both_prepare_the_photo(tmp_path: Path) -> None:
    """Bolt workers serialize the expensive handoff before reading paused state."""
    path = tmp_path / "gable.db"
    _paused_database(path)
    handoff = _handoff(path, [])
    first_download_started = threading.Event()
    release_first = threading.Event()
    downloaded_twice = threading.Event()
    download_calls = 0
    calls_lock = threading.Lock()

    def controlled_download(_url: str, _token: str, _limit: int) -> bytes:
        nonlocal download_calls
        with calls_lock:
            download_calls += 1
            call_number = download_calls
        if call_number == 1:
            first_download_started.set()
            assert release_first.wait(timeout=5)
        else:
            downloaded_twice.set()
        return _jpeg()

    object.__setattr__(handoff, "download", controlled_download)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(handoff.handle, _event(files=[{"id": "F1"}]), FakeSlackClient())
        assert first_download_started.wait(timeout=5)
        second = pool.submit(handoff.handle, _event(files=[{"id": "F2"}]), FakeSlackClient())
        # Without per-thread serialization the second worker reaches download
        # while the first still holds the run in needs_photo.
        assert not downloaded_twice.wait(timeout=0.1)
        release_first.set()
        outcomes = [first.result(timeout=5), second.result(timeout=5)]

    assert download_calls == 1
    assert "" in outcomes
    assert any("not waiting for a photo" in outcome for outcome in outcomes)


def test_photo_handoff_reports_truthful_stages_during_a_long_wait(tmp_path: Path) -> None:
    """The native indicator can move from reading through fitting to building."""
    path = tmp_path / "gable.db"
    _paused_database(path)
    stages: list[str] = []

    _handoff(path, []).handle(_event(), FakeSlackClient(), stages.append)

    assert stages == [
        "is reading the photo...",
        "is preparing the photo...",
        "is building the flyer...",
    ]


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


def test_a_tiny_upload_is_kept_for_frame_aware_upscaling_and_resumes(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []

    said = _handoff(path, seen, _jpeg(200, 200)).handle(_event(), FakeSlackClient())

    # The template runner owns any upscale because only it knows the real
    # frame. The handoff preserves the source and resumes the same run.
    assert said == ""
    assert seen == ["response-1", run_id]
    connection = connect(path)
    row = connection.execute("SELECT ai_enhanced FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row["ai_enhanced"] == 0
    connection.close()


def test_the_handoff_preserves_aspect_instead_of_cropping_twice(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    _paused_database(path)
    seen: list[str] = []
    published: list[bytes] = []
    handoff = _handoff(path, seen, _jpeg(2400, 1200))

    def publish(_root: Path, _base: str, image: bytes) -> str:
        published.append(image)
        return PUBLIC_URL

    object.__setattr__(handoff, "publish", publish)
    handoff.handle(_event(), FakeSlackClient())

    with Image.open(io.BytesIO(published[0])) as prepared:
        assert prepared.size == (2400, 1200)


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


def test_a_publish_failure_reports_server_repair_not_a_bad_image(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    _paused_database(path)
    handoff = _handoff(path, [])

    def fail_publish(_root: Path, _base: str, _fitted: bytes) -> str:
        msg = "fixed test failure"
        raise PublishError(msg)

    object.__setattr__(handoff, "publish", fail_publish)

    said = handoff.handle(_event(), FakeSlackClient())

    assert "could not save it to the flyer service" in said
    assert "different image" not in said
    connection = connect(path)
    run = store.latest_run(connection, "response-1")
    assert run is not None and run.status == "needs_photo"
    connection.close()
