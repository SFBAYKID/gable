"""The advertised ``/gable`` command surface is real, bounded, and truthful."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.slackapp.app import answer_slash_command, speaker_allowed
from gable.slackapp.commands import CommandService
from gable.voice import violations


class FakePoller:
    """Record thread-safe control requests without running a Sheet loop."""

    def __init__(self) -> None:
        """Start active, with no poll or operator request."""
        self.is_paused = False
        self.last_poll_at: datetime | None = None
        self.pass_requests = 0
        self.pause_requests = 0
        self.resume_requests = 0
        self.retries: list[str] = []
        self.rechecks: list[str] = []

    def request_pass(self) -> None:
        """Record one immediate pass request."""
        self.pass_requests += 1

    def pause(self) -> None:
        """Record and apply a pause."""
        self.pause_requests += 1
        self.is_paused = True

    def resume(self) -> None:
        """Record and apply a resume."""
        self.resume_requests += 1
        self.is_paused = False

    def queue_retry(self, run_id: str) -> bool:
        """Queue each source run only once."""
        if run_id in self.retries:
            return False
        self.retries.append(run_id)
        return True

    def queue_resume(self, run_id: str) -> bool:
        """Queue each paused run only once."""
        if run_id in self.rechecks:
            return False
        self.rechecks.append(run_id)
        return True


def _intake(label: str) -> Intake:
    """Build one minimal stored request."""
    return Intake(
        agent_email=f"{label}@example.test",
        agent_name="Test Agent",
        request_type="Sold",
        address=f"{label} Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )


def _run(path: Path, label: str, status: str) -> str:
    """Store one submission and run at a requested status."""
    connection = connect(path)
    try:
        apply_migrations(connection)
        response_id = f"response-{label}"
        store.record_submission(connection, response_id, 2, "today", _intake(label), label)
        run = store.start_run(connection, response_id)
        store.set_status(connection, run.run_id, status, "fixed test state")
        return run.run_id
    finally:
        connection.close()


def _service(path: Path, poller: FakePoller | None = None) -> tuple[CommandService, FakePoller]:
    """Build a command service over one temporary database."""
    control = poller or FakePoller()
    return CommandService(path, control, lambda: ["Sold", "New Listing"]), control


def test_status_counts_only_each_listings_latest_attempt(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    first = _run(path, "one", "failed")
    connection = connect(path)
    try:
        source = store.run_by_id(connection, first)
        assert source is not None
        later = store.start_run(connection, source.response_row_id)
        store.set_status(connection, later.run_id, "delivered", "recovered")
    finally:
        connection.close()
    _run(path, "two", "needs_photo")
    _run(path, "three", "failed")
    service, poller = _service(path)
    poller.last_poll_at = datetime(2026, 8, 12, 20, 5, tzinfo=UTC)

    message = service.handle("status").messages[0]

    assert "Pending  1" in message
    assert "Ready  1" in message
    assert "Failed  1" in message
    assert "Last poll  Aug 12 at 1:05 PM Pacific" in message
    assert not violations(message)


def test_run_queues_current_paused_work_after_a_sheet_refresh(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    paused = _run(path, "paused", "needs_template")
    obsolete = _run(path, "obsolete", "needs_photo")
    connection = connect(path)
    try:
        source = store.run_by_id(connection, obsolete)
        assert source is not None
        newer = store.start_run(connection, source.response_row_id)
        store.set_status(connection, newer.run_id, "delivered", "finished later")
    finally:
        connection.close()
    service, poller = _service(path)

    result = service.handle("run")

    assert poller.pass_requests >= 1
    assert poller.rechecks == [paused]
    assert result.messages == ("I queued a poll cycle and 1 paused listing for recheck.",)


def test_pause_and_resume_control_only_scheduled_polling(tmp_path: Path) -> None:
    service, poller = _service(tmp_path / "gable.db")

    paused = service.handle("pause")
    resumed = service.handle("resume")

    assert poller.pause_requests == 1
    assert poller.resume_requests == 1
    assert "existing Slack threads still work" in paused.messages[0]
    assert "catch-up cycle" in resumed.messages[0]


def test_retry_requires_a_real_nonactive_run_and_queues_it_once(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _run(path, "retryable", "failed")
    service, poller = _service(path)

    first = service.handle(f"retry {run_id}")
    second = service.handle(f"retry {run_id}")
    missing = service.handle("retry run-does-not-exist")

    assert poller.retries == [run_id]
    assert "queued a fresh attempt" in first.messages[0]
    assert "already queued" in second.messages[0]
    assert "could not find" in missing.messages[0]


def test_retry_refuses_an_older_run_after_a_newer_attempt_exists(tmp_path: Path) -> None:
    """Two historical run IDs cannot queue duplicate work for one listing."""
    path = tmp_path / "gable.db"
    old = _run(path, "stale", "failed")
    connection = connect(path)
    try:
        source = store.run_by_id(connection, old)
        assert source is not None
        latest = store.start_run(connection, source.response_row_id)
        store.set_status(connection, latest.run_id, "failed", "newer failure")
    finally:
        connection.close()
    service, poller = _service(path)

    message = service.handle(f"retry {old}").messages[0]

    assert "not this listing's latest run" in message
    assert latest.run_id in message
    assert poller.retries == []


def test_retry_refuses_an_active_or_exhausted_submission(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    active = _run(path, "active", "building")
    exhausted = _run(path, "exhausted", "failed")
    connection = connect(path)
    try:
        source = store.run_by_id(connection, exhausted)
        assert source is not None
        for _ in range(2):
            attempt = store.start_run(connection, source.response_row_id)
            store.set_status(connection, attempt.run_id, "failed", "fixed failure")
        exhausted = attempt.run_id
    finally:
        connection.close()
    service, poller = _service(path)

    assert "still active" in service.handle(f"retry {active}").messages[0]
    assert "three-attempt limit" in service.handle(f"retry {exhausted}").messages[0]
    assert poller.retries == []


def test_templates_lists_every_name_across_safe_bounded_messages(tmp_path: Path) -> None:
    poller = FakePoller()
    names = [f"Design {index:02d}" for index in range(60)]
    service = CommandService(tmp_path / "gable.db", poller, lambda: names)

    result = service.handle("templates")

    combined = "\n".join(result.messages)
    assert len(result.messages) > 1
    assert all(name in combined for name in names)
    assert all(len(message) <= 600 and not violations(message) for message in result.messages)


def test_unknown_or_incomplete_commands_return_the_complete_grammar(tmp_path: Path) -> None:
    service, _poller = _service(tmp_path / "gable.db")

    for command in ("", "retry", "status extra", "dance"):
        message = service.handle(command).messages[0]
        assert "/gable status" in message
        assert "/gable resume" in message
        assert not violations(message)


def test_disabled_polling_refuses_mutating_commands(tmp_path: Path) -> None:
    poller = FakePoller()
    service = CommandService(
        tmp_path / "gable.db",
        poller,
        lambda: [],
        polling_configured=False,
    )

    assert "disabled in configuration" in service.handle("run").messages[0]
    assert "disabled in configuration" in service.handle("resume").messages[0]
    assert poller.pass_requests == poller.resume_requests == 0


def test_slash_boundary_acknowledges_before_delegating_and_replies_ephemerally() -> None:
    order: list[str] = []
    responses: list[dict[str, str]] = []

    def ack() -> None:
        order.append("ack")

    def handler(text: str) -> tuple[str, ...]:
        order.append(f"handle:{text}")
        return ("Polling is active.",)

    def respond(**payload: str) -> None:
        order.append("respond")
        responses.append(payload)

    answer_slash_command(
        {"channel_id": "C1", "user_id": "UCHASE", "text": "status"},
        ack,
        respond,
        handler,
        allowed_channel="C1",
        allowed_user_ids=frozenset({"UCHASE", "UCARMEN"}),
    )

    assert order == ["ack", "handle:status", "respond"]
    assert responses == [{"text": "Polling is active.", "response_type": "ephemeral"}]


def test_wrong_channel_or_unknown_person_is_acknowledged_without_a_reply() -> None:
    for payload in (
        {"channel_id": "OTHER", "user_id": "UCHASE", "text": "status"},
        {"channel_id": "C1", "user_id": "UOTHER", "text": "status"},
    ):
        acknowledged: list[bool] = []
        responded: list[bool] = []

        def ack(target: list[bool] = acknowledged) -> None:
            target.append(True)

        def respond(target: list[bool] = responded, **_message: str) -> None:
            target.append(True)

        answer_slash_command(
            payload,
            ack,
            respond,
            lambda _text: ("This must stay private.",),
            allowed_channel="C1",
            allowed_user_ids=frozenset({"UCHASE", "UCARMEN"}),
        )
        assert acknowledged == [True]
        assert responded == []


def test_speaker_access_uses_stable_ids_not_display_names() -> None:
    allowed = frozenset({"UCHASE", "UCARMEN"})

    assert speaker_allowed("UCHASE", allowed)
    assert not speaker_allowed("Carmen", allowed)
    assert not speaker_allowed("UOTHER", allowed)
