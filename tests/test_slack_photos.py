"""Tests for resuming a paused listing from a Slack thread upload."""

from __future__ import annotations

import io
import sqlite3
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from gable.db import store
from gable.db.schema import connect
from gable.photos.store import PublishError
from gable.pipeline.runner import RunResult
from gable.sheets import repository as repo
from gable.slackapp.brain import Decision
from gable.slackapp.editing import SlideEditor
from gable.slackapp.photos import PhotoHandoffError, _SlackOnlyRedirectHandler
from gable.slackapp.recovery import notify_pending_run_questions
from tests.photo_support import (
    PUBLIC_URL,
    THREAD,
    FakeSlackClient,
    _event,
    _handoff,
    _jpeg,
    _paused_database,
)


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


def test_an_upload_satisfies_a_visible_but_unconfirmed_photo_question(
    tmp_path: Path,
) -> None:
    """The acknowledgement gap cannot reject the exact image Gable requested."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    pending = store.prepare_run_question(
        connection,
        run_id,
        "needs_photo",
        "Can you send the correct property image?",
        thread_ts=THREAD,
    )
    waiting = store.run_by_id(connection, run_id)
    assert waiting is not None and waiting.status == "needs_review"
    connection.close()

    seen: list[str] = []
    assert _handoff(path, seen).handle(_event(), FakeSlackClient()) == ""

    connection = connect(path)
    current = store.run_by_id(connection, run_id)
    assert current is not None and current.status == "delivered"
    assert current.photo_url == PUBLIC_URL
    assert seen == ["response-1", run_id]
    assert store.pending_run_questions(connection) == ()
    resolution = connection.execute(
        "SELECT confirmed_at, satisfied_at, satisfaction_detail "
        "FROM run_questions WHERE question_id = ?",
        (pending.question_id,),
    ).fetchone()
    assert resolution is not None
    assert resolution["confirmed_at"] == ""
    assert resolution["satisfied_at"]
    assert "image arrived" in resolution["satisfaction_detail"]
    connection.close()


def test_pending_question_is_satisfied_before_photo_preparation(
    tmp_path: Path,
) -> None:
    """A retry cannot post the answered question while the upload is downloading."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    pending = store.prepare_run_question(
        connection,
        run_id,
        "needs_photo",
        "Can you send the correct property image?",
        thread_ts=THREAD,
    )
    connection.close()

    handoff = _handoff(path, [])
    download_started = threading.Event()
    release_download = threading.Event()

    def blocked_download(_url: str, _token: str, _limit: int) -> bytes:
        download_started.set()
        assert release_download.wait(timeout=5)
        return _jpeg()

    object.__setattr__(handoff, "download", blocked_download)
    posts: list[str] = []

    def post_once(_text: str, _thread: str | None, client_id: str) -> str:
        posts.append(client_id)
        return "stale-question"

    with ThreadPoolExecutor(max_workers=1) as pool:
        handling = pool.submit(handoff.handle, _event(), FakeSlackClient())
        assert download_started.wait(timeout=5)
        retry_connection = connect(path)
        try:
            assert (
                notify_pending_run_questions(
                    retry_connection,
                    (pending,),
                    lambda _text, _thread: "unexpected",
                    post_once,
                )
                == 0
            )
            reserved = store.run_by_id(retry_connection, run_id)
            assert reserved is not None and reserved.status == "needs_photo"
        finally:
            retry_connection.close()
        assert posts == []
        release_download.set()
        assert handling.result(timeout=5) == ""


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


def test_a_state_change_during_refresh_cannot_attach_a_stale_photo(tmp_path: Path) -> None:
    """The final database claim must match the needs_photo state seen up front."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    handoff = _handoff(path, [])

    def change_pause(
        connection: sqlite3.Connection,
        run: store.RunRow,
    ) -> store.StoredSubmission:
        store.set_status(connection, run.run_id, "needs_info", "waiting for a direct phone")
        stored = store.load_submission(connection, run.response_row_id)
        assert stored is not None
        return stored

    class StaleRunner:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def resume(
            self,
            _submission: repo.Submission,
            stale_run_id: str,
            *,
            resume_fields: dict[str, str | int] | None = None,
            expected_status: str | None = None,
        ) -> RunResult:
            assert stale_run_id == run_id
            assert expected_status == "needs_photo"
            assert not store.claim_paused_run(
                self.connection,
                stale_run_id,
                resume_fields,
                expected_status=expected_status,
            )
            return RunResult(
                run_id=stale_run_id,
                status="needs_info",
                said=["This listing is no longer waiting for a photo."],
            )

    object.__setattr__(handoff, "load_current", change_pause)
    object.__setattr__(
        handoff,
        "runner_for",
        lambda connection, _url, _thread, _progress=None: StaleRunner(connection),
    )

    assert handoff.handle(_event(), FakeSlackClient()) == ""
    connection = connect(path)
    current = store.run_by_id(connection, run_id)
    assert current is not None
    assert current.status == "needs_info"
    assert current.photo_url == ""
    connection.close()


def test_a_durably_pending_question_suppresses_the_generic_handoff_failure(
    tmp_path: Path,
) -> None:
    """One upload cannot produce a pending exact question and conflicting fallback."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    handoff = _handoff(path, [])

    class PendingQuestionRunner:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def resume(
            self,
            _submission: repo.Submission,
            pending_run_id: str,
            *,
            resume_fields: dict[str, str | int] | None = None,
            expected_status: str | None = None,
        ) -> RunResult:
            assert expected_status == "needs_photo"
            assert store.claim_paused_run(
                self.connection,
                pending_run_id,
                resume_fields,
                expected_status=expected_status,
            )
            store.prepare_run_question(
                self.connection,
                pending_run_id,
                "needs_photo",
                "Can you send the correct property image?",
                thread_ts=THREAD,
            )
            return RunResult(run_id=pending_run_id, status="needs_review")

    object.__setattr__(
        handoff,
        "runner_for",
        lambda connection, _url, _thread, _progress=None: PendingQuestionRunner(connection),
    )

    assert handoff.handle(_event(), FakeSlackClient()) == ""
    connection = connect(path)
    current = store.run_by_id(connection, run_id)
    assert current is not None
    assert current.status == "needs_review"
    assert current.failure_reason == store.QUESTION_NOTIFICATION_PENDING
    assert len(store.pending_run_questions(connection)) == 1
    connection.close()


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


def test_values_sent_with_the_photo_are_recorded_before_the_run_resumes(
    tmp_path: Path,
) -> None:
    """Gable asks for the photo and the values together, so one reply carries both.

    The caption used to be discarded outright, and the flyer built with
    placeholders for values the person had just supplied.
    """
    path = tmp_path / "gable.db"
    _paused_database(path)
    seen: list[str] = []
    captions: list[tuple[str, str]] = []

    def record(_connection: sqlite3.Connection, address: str, text: str) -> int:
        # Ordering is the point: the values must be stored before the resume
        # that builds with them.
        assert seen == [], "the caption is read before the run is resumed"
        captions.append((address, text))
        return 2

    handoff = _handoff(path, seen, record_caption=record)

    handoff.handle(_event(text="3 beds, 2 baths"), FakeSlackClient())

    assert [text for _address, text in captions] == ["3 beds, 2 baths"]
    assert seen, "the run still resumed after the caption was read"


def test_a_caption_with_no_number_costs_no_paid_call(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    _paused_database(path)
    calls: list[str] = []

    def record(_connection: sqlite3.Connection, _address: str, text: str) -> int:
        calls.append(text)
        return 0

    _handoff(path, [], record_caption=record).handle(_event(text="here you go"), FakeSlackClient())

    assert calls == []


def test_a_caption_that_cannot_be_read_never_costs_the_photograph(
    tmp_path: Path,
) -> None:
    """The upload is the thing that matters; reading its caption is a bonus."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []

    def explode(_connection: sqlite3.Connection, _address: str, _text: str) -> int:
        raise RuntimeError("the conversational model was unavailable")

    _handoff(path, seen, record_caption=explode).handle(
        _event(text="3 beds, 2 baths"), FakeSlackClient()
    )

    connection = connect(path)
    run = store.latest_run(connection, "response-1")
    assert run is not None and run.status == "delivered"
    assert seen == ["response-1", run_id]
    connection.close()


def _deliver(path: Path, run_id: str) -> None:
    """Finish the run, as a completed flyer would."""
    connection = connect(path)
    store.set_status(connection, run_id, "delivered", "flyer delivered")
    connection.close()


def test_a_new_image_and_a_plain_request_rebuild_a_delivered_flyer(
    tmp_path: Path,
) -> None:
    """Chase's rule: send a new image, say run it again, and Gable does."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    _deliver(path, run_id)
    seen: list[str] = []

    _handoff(path, seen).handle(
        _event(text="run it again", client_msg_id="again-1"), FakeSlackClient()
    )

    assert seen == ["response-1", run_id], "the same run rebuilt, not a new attempt"
    connection = connect(path)
    assert store.run_attempt_count(connection, "response-1") == 1
    connection.close()


def test_an_image_dropped_into_a_finished_thread_changes_nothing(
    tmp_path: Path,
) -> None:
    """Requiring the words is what stops an accidental upload rebuilding it."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    _deliver(path, run_id)
    seen: list[str] = []

    said = _handoff(path, seen).handle(
        _event(text="nice one", client_msg_id="stray-1"), FakeSlackClient()
    )

    assert seen == [], "nothing was rebuilt"
    assert "not waiting for a photo" in said


def test_a_flyer_parked_in_review_accepts_a_replacement_photo(tmp_path: Path) -> None:
    """The dead end found on 2026-08-15, on an Open House run for Morgan Muse.

    The visual gate stopped the flyer because the supplied photo showed a house
    number contradicting the address — a problem only another photo can fix —
    and Gable then answered the replacement with "This listing is not waiting
    for a photo, so I left the current flyer unchanged." The one thing that
    fixes the run was the one thing the run would not take.
    """
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(
        connection,
        run_id,
        "needs_review",
        "the supplied photo shows a house number that conflicts with the address",
        slack_thread_ts=THREAD,
    )
    connection.close()
    seen: list[str] = []

    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    assert "not waiting for a photo" not in said
    assert "left the current flyer unchanged" not in said


def test_a_declined_upload_with_words_says_nothing_and_asks_to_be_answered(
    tmp_path: Path,
) -> None:
    """The first fix compared the declining sentence and never fired in production.

    The real handler posts through the durable outbox and returns an empty
    string, so the sentence never reached the caller to be recognised. Tambria
    Eaton's address was refused twice for that reason. The signal is a sentinel
    the outbox cannot swallow.
    """
    from gable.slackapp.photos import DECLINED_ANSWER_THE_WORDS

    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(
        connection, run_id, "needs_info", "waiting for the address", slack_thread_ts=THREAD
    )
    connection.close()

    said = _handoff(path, []).handle(
        _event(text="1011 Winged Foot Dr, Westminster, MD 21158"),
        FakeSlackClient(),
    )

    assert said == DECLINED_ANSWER_THE_WORDS


def test_a_declined_upload_with_no_words_still_explains_itself(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(
        connection, run_id, "needs_info", "waiting for the address", slack_thread_ts=THREAD
    )
    connection.close()

    said = _handoff(path, []).handle(_event(), FakeSlackClient())

    assert "not waiting for a photo" in said
