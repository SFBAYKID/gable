"""The native Slack waiting state covers every user-triggered response.

The tests lock down the purple-status API, the requested timed copy, truthful
long-work stages, follow-up coverage, and cleanup before any live deployment.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from gable.slackapp import status
from gable.slackapp.status import Working


class FakeClient:
    """Record native Slack status calls and optionally reject them."""

    def __init__(self, *, status_works: bool = True) -> None:
        """Start with a working native status surface."""
        self.status_works = status_works
        self.statuses: list[dict[str, str]] = []

    def assistant_threads_setStatus(self, **kwargs: Any) -> None:  # noqa: ANN401, N802
        """Stand in for Slack's assistant.threads.setStatus method."""
        if not self.status_works:
            raise RuntimeError("fixed test failure")
        self.statuses.append({key: str(value) for key, value in kwargs.items()})


def _texts(client: FakeClient) -> list[str]:
    """Return only the status text from recorded Slack calls."""
    return [call["status"] for call in client.statuses]


def _wait_until(condition: Callable[[], bool], seconds: float = 1.0) -> None:
    """Wait for a background status update without choosing a fixed long sleep."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not condition():
        time.sleep(0.002)


def _use_fast_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the production sequence while making its timing cheap to test."""
    monkeypatch.setattr(
        status,
        "LEAD_IN_STEPS",
        (
            (0.01, "says hold tight..."),
            (0.01, "is jittering..."),
            (0.01, "is bobbing and weaving..."),
        ),
    )
    monkeypatch.setattr(status, "FINAL_LEAD_IN_SECONDS", 0.01)
    monkeypatch.setattr(status, "STAGE_REFRESH_SECONDS", 0.01)


def test_requested_production_timing_is_one_then_two_second_holds() -> None:
    """The copy changes at one, three, five, then six seconds."""
    assert status.LEAD_IN_STEPS == (
        (1.0, "says hold tight..."),
        (2.0, "is jittering..."),
        (2.0, "is bobbing and weaving..."),
    )
    assert status.FINAL_LEAD_IN_SECONDS == 1.0


def test_native_purple_state_starts_immediately_and_clears_afterward() -> None:
    """A fast answer still flashes Slack's native Gable waiting treatment."""
    client = FakeClient()

    with Working(client, "C0B02721MNK", "111.1"):
        assert _texts(client) == [status.INITIAL_STATUS]

    assert _texts(client) == [status.INITIAL_STATUS, ""]
    assert client.statuses[0]["channel_id"] == "C0B02721MNK"
    assert client.statuses[0]["thread_ts"] == "111.1"


def test_long_wait_runs_the_playful_sequence_then_real_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After six seconds, personality gives way to a truthful workflow stage."""
    _use_fast_timing(monkeypatch)
    client = FakeClient()

    with Working(client, "C1", "111.1", "is building the flyer..."):
        _wait_until(lambda: "is building the flyer..." in _texts(client))

    texts = _texts(client)
    assert texts[:5] == [
        "is thinking...",
        "says hold tight...",
        "is jittering...",
        "is bobbing and weaving...",
        "is building the flyer...",
    ]
    assert texts[-1] == ""


def test_latest_real_stage_replaces_an_earlier_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A photo wait can move honestly from fitting to building."""
    _use_fast_timing(monkeypatch)
    client = FakeClient()

    with Working(client, "C1", "111.1", "is fitting the photo...") as waiting:
        waiting.stage("is building the flyer...")
        _wait_until(lambda: "is building the flyer..." in _texts(client))

    assert "is building the flyer..." in _texts(client)


def test_indicator_clears_even_when_the_work_raises() -> None:
    """A failed response cannot leave Gable claiming it is still thinking."""
    client = FakeClient()

    with pytest.raises(ValueError, match="thinking failed"), Working(client, "C1", "111.1"):
        raise ValueError("thinking failed")

    assert _texts(client)[-1] == ""


def test_broken_native_status_never_breaks_the_response() -> None:
    """Cosmetic Slack trouble must never affect the work it describes."""
    client = FakeClient(status_works=False)
    completed = False

    with Working(client, "C1", "111.1"):
        completed = True

    assert completed is True


def test_a_stalled_initial_status_never_delays_the_real_work() -> None:
    """Cosmetic Slack I/O stays off the event-handler thread during an outage."""
    entered = threading.Event()
    release = threading.Event()

    class Stalled(FakeClient):
        def assistant_threads_setStatus(self, **kwargs: Any) -> None:  # noqa: ANN401, N802
            entered.set()
            release.wait(timeout=1)
            super().assistant_threads_setStatus(**kwargs)

    client = Stalled()
    started = time.monotonic()
    waiting = Working(client, "C1", "111.1")
    waiting.start()

    try:
        assert entered.wait(timeout=0.2)
        assert time.monotonic() - started < 0.25
        assert not waiting._done.is_set()
    finally:
        release.set()
        waiting.stop()

    _wait_until(lambda: bool(client.statuses), seconds=1.0)
    _wait_until(lambda: _texts(client)[-1:] == [""], seconds=1.0)
    assert _texts(client) == [status.INITIAL_STATUS, ""]


def test_missing_thread_disables_status_instead_of_broadcasting() -> None:
    """Status belongs to a response thread and nowhere else."""
    client = FakeClient()

    with Working(client, "C1", ""):
        pass

    assert client.statuses == []


class _ConversationClient(FakeClient):
    """A native-status client that also resolves the speaker's first name."""

    def users_info(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        """Stand in for users.info."""
        return {"user": {"profile": {"first_name": "Chase"}}}


def _decision(reply: str, tool: str = "") -> Any:  # noqa: ANN401
    """Build a conversational decision without importing it at module load."""
    from gable.slackapp.brain import Decision

    return Decision(reply=reply, tool=tool, arguments={})


def _thinker(reply: str) -> Any:  # noqa: ANN401
    """Return a thinker with the production speaker keyword signature."""

    def think_it(_asked: str, speaker: str = "") -> Any:  # noqa: ANN401
        del speaker
        return _decision(reply)

    return think_it


def test_initial_mention_answers_in_thread_before_native_state_clears() -> None:
    """The first mention opens a thread, answers there, then ends waiting."""
    from gable.slackapp.app import answer_mention

    order: list[str] = []

    class Ordered(_ConversationClient):
        def assistant_threads_setStatus(self, **kwargs: Any) -> None:  # noqa: ANN401, N802
            text = str(kwargs.get("status", ""))
            order.append(f"status:{text}")
            super().assistant_threads_setStatus(**kwargs)

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        order.append(f"answer:{kwargs['text']}")
        assert kwargs["thread_ts"] == "1.1"
        return {"ts": "said"}

    answer_mention(
        {"channel": "C0B02721MNK", "ts": "1.1", "text": "<@U1> hello", "user": "U9"},
        say,
        Ordered(),
        _thinker("Hey Chase — what can I do for you?"),
    )

    assert order == [
        "status:is thinking...",
        "answer:Hey Chase — what can I do for you?",
        "status:",
    ]


def test_follow_up_question_gets_the_same_native_waiting_state() -> None:
    """Every later question in the thread behaves like the first mention."""
    from gable.slackapp.app import answer_thread_reply

    client = _ConversationClient()
    said: list[dict[str, Any]] = []

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        """Record the follow-up answer."""
        said.append(kwargs)
        return {"ts": "said"}

    answer_thread_reply(
        {
            "channel": "C0B02721MNK",
            "thread_ts": "1.1",
            "ts": "2.2",
            "text": "what do you need?",
            "user": "U9",
        },
        say,
        client,
        _thinker("Send me the property photo."),
    )

    assert _texts(client) == ["is thinking...", ""]
    assert said == [{"text": "Send me the property photo.", "thread_ts": "1.1"}]


def test_an_action_that_posted_via_the_outbox_is_not_posted_again() -> None:
    """The listener clears native status without duplicating an outbox message."""
    from gable.slackapp.app import answer_thread_reply
    from gable.slackapp.brain import Decision

    client = _ConversationClient()
    said: list[dict[str, Any]] = []

    answer_thread_reply(
        {
            "channel": "C0B02721MNK",
            "thread_ts": "1.1",
            "ts": "2.2",
            "text": "rerun this project",
            "user": "U9",
        },
        lambda **kwargs: said.append(kwargs),
        client,
        lambda _message, **_kwargs: Decision(
            reply="I will rebuild it.",
            tool="rebuild_flyer",
            arguments={"mode": "check_updated"},
        ),
        action_handler=lambda _decision, _thread, _progress, _action_id: "",
    )

    # The real stage is set synchronously, but the native status worker may
    # coalesce it with immediate cleanup when the action finishes instantly.
    assert _texts(client)[0] == "is thinking..."
    assert _texts(client)[-1] == ""
    assert said == []


def test_action_decisions_name_the_work_instead_of_staying_generic() -> None:
    """Long edits show what Gable is changing after the playful lead-in."""
    from gable.slackapp.app import stage_for_decision

    assert stage_for_decision(_decision("", "rebuild_flyer")) == "is rebuilding the flyer..."
    assert stage_for_decision(_decision("")) == "is preparing the answer..."


def test_thinking_failure_reports_before_native_state_clears() -> None:
    """Even the failure sentence arrives while Slack still shows Gable working."""
    from gable.slackapp.app import FALLBACK, answer_mention

    order: list[str] = []

    class Ordered(_ConversationClient):
        def assistant_threads_setStatus(self, **kwargs: Any) -> None:  # noqa: ANN401, N802
            order.append(f"status:{kwargs.get('status', '')}")
            super().assistant_threads_setStatus(**kwargs)

    def explode(_asked: str, speaker: str = "") -> Any:  # noqa: ANN401
        del speaker
        raise RuntimeError("fixed model failure")

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        """Record the plain-language failure."""
        order.append(f"answer:{kwargs['text']}")
        return {"ts": "said"}

    answer_mention(
        {"channel": "C0B02721MNK", "ts": "1.1", "text": "hello", "user": "U9"},
        say,
        Ordered(),
        explode,
    )

    assert order == ["status:is thinking...", f"answer:{FALLBACK}", "status:"]


def test_an_empty_stage_cannot_clear_the_indicator_before_the_answer() -> None:
    """Only the response context clears status after its message is posted."""
    client = FakeClient()
    waiting = Working(client, "C1", "1786.1")
    waiting.start()
    waiting.stage("")

    try:
        assert not waiting._done.is_set()
        assert client.statuses[-1]["status"] == "is thinking..."
    finally:
        waiting.stop()


def test_a_real_stage_keeps_the_indicator_running() -> None:
    client = FakeClient()
    waiting = Working(client, "C1", "1786.1")
    waiting.start()
    waiting.stage("is placing the photo...")
    try:
        assert not waiting._done.is_set()
    finally:
        waiting.stop()
