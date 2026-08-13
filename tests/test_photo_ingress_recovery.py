"""A photo upload accepted but never finished must be requested again.

Satisfying the pending question before the slow download is what stops a
delivery worker reposting it mid-upload. That is only safe because the handoff
claims a durable ingress row first, so these tests pin the recovery half of that
bargain rather than trusting it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import connect
from gable.slackapp.photos import PhotoHandoff
from gable.slackapp.recovery import recover_abandoned_photo_uploads
from tests.photo_support import (
    THREAD,
    FakeSlackClient,
    _event,
    _handoff,
    _paused_database,
)


def _crashing_handoff(path: Path) -> PhotoHandoff:
    """A handoff whose download dies the way a killed process does."""
    handoff = _handoff(path, [])

    def dying_download(_url: str, _token: str, _limit: int) -> bytes:
        # BaseException escapes the handler's `except Exception` boundaries, so
        # this leaves exactly the durable state a SIGKILL would.
        raise KeyboardInterrupt

    object.__setattr__(handoff, "download", dying_download)
    return handoff


def test_an_upload_interrupted_before_its_outcome_is_requested_again(
    tmp_path: Path,
) -> None:
    """A process death mid-preparation must not swallow Carmen's only upload."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.prepare_run_question(
        connection,
        run_id,
        "needs_photo",
        "Can you send the correct property image?",
        thread_ts=THREAD,
    )
    connection.close()

    with pytest.raises(KeyboardInterrupt):
        _crashing_handoff(path).handle(_event(), FakeSlackClient())

    restarted = connect(path)
    # The upload was accepted, so the original question is retired and nothing
    # re-posts it; without recovery the listing would wait forever.
    assert store.pending_run_questions(restarted) == ()
    assert store.abandoned_slack_events(restarted, "file_share")

    assert recover_abandoned_photo_uploads(restarted) == (run_id,)

    pending = store.pending_run_questions(restarted)
    assert len(pending) == 1
    assert "send it once more" in pending[0].message
    assert pending[0].thread_ts == THREAD
    # The claim is released, so a second recovery pass cannot ask a third time.
    assert store.abandoned_slack_events(restarted, "file_share") == ()
    assert recover_abandoned_photo_uploads(restarted) == ()
    assert len(store.pending_run_questions(restarted)) == 1
    restarted.close()


def test_recovery_releases_a_claim_whose_upload_actually_finished(
    tmp_path: Path,
) -> None:
    """Only the release was lost, so the flyer must not be re-requested."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []
    assert _handoff(path, seen).handle(_event(), FakeSlackClient()) == ""

    connection = connect(path)
    # Reopen the durable claim to model a crash between the run claim and the
    # ingress release.
    connection.execute("UPDATE slack_event_claims SET completed_at = '' WHERE route = 'file_share'")
    assert store.abandoned_slack_events(connection, "file_share")

    assert recover_abandoned_photo_uploads(connection) == ()
    assert store.pending_run_questions(connection) == ()
    assert store.abandoned_slack_events(connection, "file_share") == ()
    current = store.run_by_id(connection, run_id)
    assert current is not None and current.status == "delivered"
    connection.close()


def test_a_duplicate_photo_event_stays_silent_after_a_restart(
    tmp_path: Path,
) -> None:
    """Slack redelivering the same file must not rebuild or speak twice."""
    path = tmp_path / "gable.db"
    _paused_database(path)
    seen: list[str] = []
    assert _handoff(path, seen).handle(_event(), FakeSlackClient()) == ""
    assert len(seen) == 2

    # A fresh handoff has no process memory of the first delivery.
    repeated: list[str] = []
    assert _handoff(path, repeated).handle(_event(), FakeSlackClient()) == ""
    assert repeated == []


def test_recovery_never_contradicts_a_message_the_thread_is_already_owed(
    tmp_path: Path,
) -> None:
    """An owed exact message wins; a second request would talk over it."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.prepare_run_question(
        connection,
        run_id,
        "needs_photo",
        "Can you send the correct property image?",
        thread_ts=THREAD,
    )
    connection.close()

    with pytest.raises(KeyboardInterrupt):
        _crashing_handoff(path).handle(_event(), FakeSlackClient())

    restarted = connect(path)
    owed = store.prepare_run_question(
        restarted,
        run_id,
        "needs_photo",
        "A different exact message already owes this thread.",
        thread_ts=THREAD,
    )
    assert recover_abandoned_photo_uploads(restarted) == ()
    pending = store.pending_run_questions(restarted)
    assert [item.question_id for item in pending] == [owed.question_id]
    assert store.abandoned_slack_events(restarted, "file_share") == ()
    restarted.close()
