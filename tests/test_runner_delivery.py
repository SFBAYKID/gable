"""Runner delivery, photo handoff, and Slack-confirmation invariants."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from tests.runner_support import Recorder
from tests.runner_support import record as _record
from tests.runner_support import runner as _runner
from tests.runner_support import submission as _submission


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Return a migrated runner-test database."""
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


def test_delivery_is_recorded_only_after_slack_confirms_its_message(
    db: sqlite3.Connection,
) -> None:
    """A ready Drive file is still building until its link exists in Slack."""
    submission = _submission(rid="rid-delivery-order")
    _record(db, submission)
    runner = _runner(db, Recorder())
    status_during_post: list[str] = []

    def say(_message: str, _thread_ts: str | None) -> str:
        current = store.latest_run(db, submission.response_row_id)
        assert current is not None
        status_during_post.append(current.status)
        return "1786468156.900001"

    runner.say = say
    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert status_during_post == ["building"]
    assert result.status == "delivered"
    assert current is not None
    assert current.status == "delivered"
    assert current.slack_thread_ts == "1786468156.900001"
    delivered_events = db.execute(
        "SELECT detail FROM run_events WHERE run_id = ? AND status = 'delivered'",
        (result.run_id,),
    ).fetchall()
    assert [event["detail"] for event in delivered_events] == [
        "Slack confirmed the delivery message"
    ]


def test_an_unconfirmed_delivery_message_never_leaves_a_delivered_run(
    db: sqlite3.Connection,
) -> None:
    """A blank Slack timestamp is not evidence that the link reached Carmen."""
    submission = _submission(rid="rid-delivery-unconfirmed")
    _record(db, submission)
    runner = _runner(db, Recorder())
    runner.say = lambda _message, _thread_ts: ""

    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert result.status == "failed"
    assert current is not None
    assert current.status == "failed"
    assert current.output_url.endswith("/edit")
    assert not db.execute(
        "SELECT 1 FROM run_events WHERE run_id = ? AND status = 'delivered'",
        (result.run_id,),
    ).fetchone()


def test_a_slack_delivery_outage_is_recorded_without_escaping_the_runner(
    db: sqlite3.Connection,
) -> None:
    """Slack can fail both the link and failure notice without falsifying state."""
    submission = _submission(rid="rid-delivery-slack-down")
    _record(db, submission)
    runner = _runner(db, Recorder())

    def unavailable(_message: str, _thread_ts: str | None) -> str:
        raise RuntimeError("test Slack outage")

    runner.say = unavailable
    result = runner.run(submission)

    current = store.run_by_id(db, result.run_id)
    assert result.status == "failed"
    assert current is not None and current.status == "failed"
    assert not db.execute(
        "SELECT 1 FROM run_events WHERE run_id = ? AND status = 'delivered'",
        (result.run_id,),
    ).fetchone()


def test_no_flyer_is_delivered_without_a_hero_photo(db: sqlite3.Connection) -> None:
    """A listing flyer showing the template's own placeholder is not a draft."""
    submission = _submission(rid="rid-nophoto")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.hero_photo_url = ""
    result = runner.run(submission)

    assert result.status == "needs_photo"
    assert rec.copied is False, "nothing should be built before there is a photo"
    headline, question = rec.said[0], rec.said[1]
    assert submission.intake.address in headline
    assert rec.threads[0] is None, "the announcement is the root of the thread"
    assert rec.threads[1] == "1786.0", "the question is a reply underneath it"
    assert "send me the image" in question.lower()
    assert "hero" not in question.lower()


def test_an_unusable_photo_url_stops_before_a_flyer_is_copied(
    db: sqlite3.Connection,
) -> None:
    """The live URL check is injected and a rejection pauses the run safely."""
    submission = _submission(rid="rid-bad-photo-url")
    _record(db, submission)
    rec = Recorder()
    runner = _runner(db, rec)
    runner.check_photo = lambda _url, _slot: (False, "that image link did not load")

    result = runner.run(submission)

    assert result.status == "needs_photo"
    assert rec.copied is False
    assert any("did not load" in said for said in rec.said)


def test_a_photo_resumes_the_existing_run_without_opening_another(
    db: sqlite3.Connection,
) -> None:
    """A Slack upload continues the paused audit trail instead of forking it."""
    submission = _submission(rid="rid-resume-photo")
    _record(db, submission)
    rec = Recorder()
    waiting = _runner(db, rec)
    waiting.hero_photo_url = ""
    paused = waiting.run(submission)
    assert paused.status == "needs_photo"

    resumed = _runner(db, rec).resume(submission, paused.run_id)

    assert resumed.status == "delivered"
    assert store.run_attempt_count(db, submission.response_row_id) == 1
    statuses = [
        row["status"]
        for row in db.execute(
            "SELECT status FROM run_events WHERE run_id = ? ORDER BY id", (paused.run_id,)
        ).fetchall()
    ]
    assert "needs_photo" in statuses
    assert statuses[-1] == "delivered"


def test_a_second_resume_does_not_build_a_duplicate_flyer(db: sqlite3.Connection) -> None:
    """The first paused-run claim wins even when another event has stale context."""
    submission = _submission(rid="rid-resume-once")
    _record(db, submission)
    waiting = _runner(db, Recorder())
    waiting.hero_photo_url = ""
    paused = waiting.run(submission)

    first_rec = Recorder()
    first = _runner(db, first_rec).resume(submission, paused.run_id)
    second_rec = Recorder()
    second = _runner(db, second_rec).resume(submission, paused.run_id)

    assert first.status == "delivered"
    assert second.status == "delivered"
    assert first_rec.copied is True
    assert second_rec.copied is False
    assert "another copy" in second_rec.said[-1]


def test_a_photo_that_will_not_go_on_stops_delivery(db: sqlite3.Connection) -> None:
    """Given a photo and unable to place it, stopping beats shipping without."""
    submission = _submission(rid="rid-photofail")
    _record(db, submission)

    class NoPlace(Recorder):
        def place_photo(
            self,
            _run_id: str,
            _file_id: str,
            _url: str,
            _template_label: str,
        ) -> bool:
            """Fail the way a rejected image URL would."""
            return False

    rec = NoPlace()
    result = _runner(db, rec).run(submission)
    assert result.status == "needs_review"
    assert "could not get the photo onto it" in rec.said[-1]


def test_the_photo_is_placed_on_a_delivered_flyer(db: sqlite3.Connection) -> None:
    submission = _submission(rid="rid-photook")
    _record(db, submission)
    rec = Recorder()
    result = _runner(db, rec).run(submission)
    assert result.status == "delivered"
    assert rec.photo_placed is True


def test_an_unsafe_text_match_stops_before_photo_placement(db: sqlite3.Connection) -> None:
    class UnsafeRecorder(Recorder):
        def fill(self, file_id: str, pairs: dict[str, str]) -> int:  # noqa: ARG002
            self.filled = pairs
            return -1

    rec = UnsafeRecorder()
    submission = _submission(rid="rid-unsafe-text")
    _record(db, submission)
    result = _runner(db, rec).run(submission)

    assert result.status == "needs_review"
    assert rec.photo_placed is False
    assert "did not match exactly once" in result.said[-1]
