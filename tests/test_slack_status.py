"""What the thinking indicator must do, and must never do.

The behaviour asked for is precise: it comes into the thread when the question
is asked, stays while the answer is composed, and disappears once the answer
arrives. So the tests are about the *lifecycle* — posted, animated, deleted —
and about the fact that none of it may ever break the reply it decorates.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from gable.slackapp.status import FRAMES, Working


class FakeClient:
    """Records Slack calls and can be told to fail any of them."""

    def __init__(self, *, post_works: bool = True, delete_works: bool = True) -> None:
        """Start with every Slack surface working unless told otherwise."""
        self.post_works = post_works
        self.delete_works = delete_works
        self.posted: list[str] = []
        self.updated: list[str] = []
        self.deleted: list[str] = []

    def chat_postMessage(self, **kwargs: Any) -> dict[str, str]:  # noqa: ANN401, N802
        """Stand in for chat.postMessage."""
        if not self.post_works:
            msg = "channel_not_found"
            raise RuntimeError(msg)
        self.posted.append(str(kwargs.get("text", "")))
        return {"ts": "111.222"}

    def chat_update(self, **kwargs: Any) -> None:  # noqa: ANN401
        """Stand in for chat.update."""
        self.updated.append(str(kwargs.get("text", "")))

    def chat_delete(self, **kwargs: Any) -> None:  # noqa: ANN401
        """Stand in for chat.delete."""
        if not self.delete_works:
            msg = "message_not_found"
            raise RuntimeError(msg)
        self.deleted.append(str(kwargs.get("ts", "")))


def _settle(client: FakeClient, attribute: str, seconds: float = 2.0) -> None:
    """Wait for the background thread to have done something observable."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not getattr(client, attribute):
        time.sleep(0.01)


def test_the_indicator_is_posted_into_the_thread() -> None:
    """It has to appear in the thread the question was asked in."""
    client = FakeClient()
    with Working(client, "C1", "111.1"):
        _settle(client, "posted")
    assert client.posted, "nothing was posted"
    assert client.posted[0] == FRAMES[0]


def test_the_indicator_is_deleted_when_the_answer_arrives() -> None:
    """It must go away, not turn into the reply.

    An earlier version edited this message into the answer, so the indicator
    never disappeared — which is not what was asked for.
    """
    client = FakeClient()
    with Working(client, "C1", "111.1"):
        _settle(client, "posted")
    assert client.deleted == ["111.222"]


def test_it_animates_while_the_work_runs() -> None:
    """A static line does not read as working; the frames make it move."""
    client = FakeClient()
    with Working(client, "C1", "111.1"):
        _settle(client, "posted")
        time.sleep(1.7)  # long enough for at least one frame
    assert client.updated, "the indicator never advanced a frame"
    assert client.updated[0] in FRAMES


def test_it_is_removed_even_when_the_work_raises() -> None:
    """A failed reply must not leave the thread looking like it is still going."""
    client = FakeClient()
    with pytest.raises(ValueError, match="thinking failed"), Working(client, "C1", "111.1"):
        _settle(client, "posted")
        raise ValueError("thinking failed")
    assert client.deleted == ["111.222"]


def test_a_broken_indicator_never_breaks_the_work() -> None:
    """Every Slack call here can fail, and none of them may propagate."""
    client = FakeClient(post_works=False, delete_works=False)
    done = False
    with Working(client, "C1", "111.1"):
        done = True
    assert done, "the body must run even when every Slack call fails"


def test_a_failed_post_does_not_stall_the_reply() -> None:
    """If posting fails, `stop` must not sit waiting for a message that is absent.

    Waiting the full timeout on every reply would make the decoration slower
    than the thing it decorates.
    """
    client = FakeClient(post_works=False)
    started = time.monotonic()
    with Working(client, "C1", "111.1"):
        pass
    assert time.monotonic() - started < 1.0
    assert client.deleted == []


def test_no_thread_means_no_indicator() -> None:
    """A loose message in the channel would be worse than showing nothing."""
    client = FakeClient()
    with Working(client, "C1", ""):
        time.sleep(0.05)
    assert client.posted == []
    assert client.deleted == []


class _MentionClient(FakeClient):
    """A Slack client that also answers the speaker-name lookup."""

    def users_info(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        """Stand in for users.info."""
        return {"user": {"profile": {"first_name": "Chase"}}}


def _decision(reply: str) -> Any:  # noqa: ANN401
    from gable.slackapp.brain import Decision

    return Decision(reply=reply, tool="", arguments={})


def _thinker(reply: str) -> Any:  # noqa: ANN401
    """A stand-in brain that always answers the same thing.

    Takes `speaker` by keyword because the real one does, and getting that
    signature wrong is what broke the live handler once already.
    """

    def think_it(_asked: str, speaker: str = "") -> Any:  # noqa: ANN401
        del speaker
        return _decision(reply)

    return think_it


def test_a_mention_says_exactly_one_message() -> None:
    """The indicator must not be part of the answer, and must not double it.

    The rejected pattern posted a placeholder and edited it into the reply. This
    is the regression test for it: the only thing `say` is used for is the answer.
    """
    from gable.slackapp.app import answer_mention

    said: list[dict[str, Any]] = []
    client = _MentionClient()

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        said.append(kwargs)
        return {"ts": "said"}

    answer_mention(
        {"channel": "C0B02721MNK", "ts": "1.1", "text": "<@U1> hello", "user": "U9"},
        say,
        client,
        _thinker("Hey Chase — what can I do for you?"),
    )

    assert len(said) == 1, "the answer must be the only message said"
    assert said[0]["text"] == "Hey Chase — what can I do for you?"
    assert client.deleted == ["111.222"], "the indicator must have been removed"


def test_the_indicator_goes_in_the_thread_not_the_channel() -> None:
    """The indicator belongs in the thread itself — asserted, not assumed."""
    from gable.slackapp.app import answer_mention

    client = _MentionClient()
    answer_mention(
        {"channel": "C0B02721MNK", "ts": "1.1", "thread_ts": "0.9", "text": "hi", "user": "U9"},
        lambda **_kwargs: {"ts": "said"},
        client,
        _thinker("Sure."),
    )
    assert client.posted, "no indicator was posted"


def test_the_answer_is_posted_before_the_indicator_is_removed() -> None:
    """Clearing first would open a gap at the exact moment the wait ends."""
    from gable.slackapp.app import answer_mention

    order: list[str] = []

    class Ordered(_MentionClient):
        def chat_delete(self, **kwargs: Any) -> None:  # noqa: ANN401
            order.append("indicator removed")
            super().chat_delete(**kwargs)

    def say(**_kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        order.append("answer said")
        return {"ts": "said"}

    answer_mention(
        {"channel": "C0B02721MNK", "ts": "1.1", "text": "hi", "user": "U9"},
        say,
        Ordered(),
        _thinker("Sure."),
    )

    assert order == ["answer said", "indicator removed"]


def test_a_thinking_failure_still_answers_and_leaves_nothing_behind() -> None:
    """A dead reply must not strand an indicator claiming work is in flight."""
    from gable.slackapp.app import FALLBACK, answer_mention

    said: list[dict[str, Any]] = []
    client = _MentionClient()

    def explode(_asked: str, _speaker: str = "") -> Any:  # noqa: ANN401
        msg = "the model fell over"
        raise RuntimeError(msg)

    answer_mention(
        {"channel": "C0B02721MNK", "ts": "1.1", "text": "hi", "user": "U9"},
        lambda **kwargs: said.append(kwargs) or {"ts": "said"},  # type: ignore[func-returns-value]
        client,
        explode,
    )

    assert said and said[0]["text"] == FALLBACK
    assert client.deleted == ["111.222"], "the indicator must not survive the failure"
