"""Regression tests for Slack thread ownership and mention routing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from gable.slackapp.app import answer_mention, build_app, process_mention
from gable.slackapp.brain import Decision
from gable.slackapp.routing import EventReplayGuard, MessageRoute, ThreadOwnership

CHANNEL = "C0B02721MNK"
GABLE_USER = "UGABLE"
GABLE_BOT = "BGABLE"


def _event(**overrides: object) -> dict[str, Any]:
    """Build one ordinary human reply event."""
    event: dict[str, Any] = {
        "channel": CHANNEL,
        "thread_ts": "1786.1",
        "ts": "1786.2",
        "user": "UCHASE",
        "text": "Number 13 and 22",
    }
    event.update(overrides)
    return event


class RootClient:
    """Return configured Slack thread roots and record each lookup."""

    def __init__(self, roots: dict[str, dict[str, Any]] | None = None) -> None:
        """Store root messages by thread timestamp."""
        self.roots = roots or {}
        self.calls: list[tuple[str, str, int]] = []

    def conversations_replies(self, *, channel: str, ts: str, limit: int) -> dict[str, Any]:
        """Stand in for Slack's conversations.replies method."""
        self.calls.append((channel, ts, limit))
        root = self.roots.get(ts)
        return {"messages": [root] if root is not None else []}


def test_plain_reply_in_another_agents_thread_is_ignored_and_cached() -> None:
    """The reported Monarch keyword thread cannot wake Gable."""
    client = RootClient(
        {
            "1786.1": {
                "user": "UMONARCH",
                "bot_id": "BMONARCH",
                "text": "Blog keywords for 2026-08-13 — pick two numbers",
            }
        }
    )
    ownership = ThreadOwnership()

    first = ownership.route(
        _event(parent_user_id="UMONARCH"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )
    second = ownership.route(
        _event(parent_user_id="UMONARCH", ts="1786.3"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert first is MessageRoute.IGNORE
    assert second is MessageRoute.IGNORE
    assert client.calls == [(CHANNEL, "1786.1", 1)]


def test_plain_reply_in_a_gable_authored_thread_needs_no_mention_or_lookup() -> None:
    """A listing thread rooted by Gable remains conversational by default."""
    client = RootClient()

    route = ThreadOwnership().route(
        _event(parent_user_id=GABLE_USER, text="Can you send me the image?"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.THREAD_REPLY
    assert client.calls == []


def test_photo_in_a_gable_authored_thread_routes_to_the_handoff() -> None:
    """The ownership guard preserves automatic listing photo replies."""
    route = ThreadOwnership().route(
        _event(parent_user_id=GABLE_USER, subtype="file_share", files=[{"id": "F1"}]),
        RootClient(),
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.FILE_SHARE


def test_current_file_event_without_legacy_subtype_routes_to_the_handoff() -> None:
    """Slack's current file payload is identified by its files array."""
    route = ThreadOwnership().route(
        _event(parent_user_id=GABLE_USER, files=[{"id": "F1"}]),
        RootClient(),
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.FILE_SHARE


def test_a_thread_broadcast_is_still_an_owned_human_reply() -> None:
    """Checking “also send to channel” must not make Gable ignore the reply."""
    route = ThreadOwnership().route(
        _event(parent_user_id=GABLE_USER, subtype="thread_broadcast"),
        RootClient(),
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.THREAD_REPLY


def test_follow_up_to_a_human_root_that_called_gable_remains_owned() -> None:
    """A top-level app mention starts a Gable conversation after the first reply."""
    client = RootClient(
        {
            "1786.1": {
                "user": "UCHASE",
                "text": "<@UGABLE> help me with this listing",
            }
        }
    )

    route = ThreadOwnership(allowed_user_ids=frozenset({"UCHASE", "UCARMEN"})).route(
        _event(parent_user_id="UCHASE", text="What do you need from me?"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.THREAD_REPLY


def test_a_foreign_bot_root_that_mentions_gable_does_not_transfer_ownership() -> None:
    """One mention in another agent's root authorizes no later plain replies."""
    client = RootClient(
        {
            "1786.1": {
                "user": "UMONARCH",
                "bot_id": "BMONARCH",
                "app_id": "AMONARCH",
                "text": "A Monarch result for <@UGABLE> to inspect",
            }
        }
    )

    route = ThreadOwnership(allowed_user_ids=frozenset({"UCHASE", "UCARMEN"})).route(
        _event(parent_user_id="UMONARCH", text="yes"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.IGNORE


def test_an_unauthorized_human_root_cannot_create_an_owned_gable_thread() -> None:
    """Visible mention text is not a substitute for the stable-user allowlist."""
    client = RootClient(
        {
            "1786.1": {
                "user": "UOTHER",
                "text": "<@UGABLE> help me with this listing",
            }
        }
    )

    route = ThreadOwnership(allowed_user_ids=frozenset({"UCHASE", "UCARMEN"})).route(
        _event(parent_user_id="UOTHER", text="continue"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.IGNORE


def test_an_unaddressed_human_thread_is_also_foreign() -> None:
    """Thread ownership depends on its root, not whether its author was a bot."""
    client = RootClient({"1786.1": {"user": "UCARMEN", "text": "Campaign notes"}})

    route = ThreadOwnership().route(
        _event(parent_user_id="UCARMEN"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.IGNORE


def test_top_level_and_bot_authored_messages_are_ignored_without_a_lookup() -> None:
    """Only human replies inside an owned thread reach either workflow."""
    client = RootClient()
    ownership = ThreadOwnership()

    top_level = ownership.route(
        _event(thread_ts=""),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )
    bot_message = ownership.route(
        _event(bot_id="BMONARCH"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert top_level is MessageRoute.IGNORE
    assert bot_message is MessageRoute.IGNORE
    assert client.calls == []


def test_a_root_lookup_failure_stays_silent_and_can_retry() -> None:
    """Slack read trouble cannot make Gable intrude on an unknown thread."""

    class BrokenClient:
        """Reject every root lookup."""

        def __init__(self) -> None:
            self.calls = 0

        def conversations_replies(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            """Raise a fixed Slack-like failure."""
            self.calls += 1
            raise RuntimeError("fixed root lookup failure")

    client = BrokenClient()
    ownership = ThreadOwnership()

    first = ownership.route(
        _event(parent_user_id="UMONARCH"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )
    second = ownership.route(
        _event(parent_user_id="UMONARCH", ts="1786.3"),
        client,
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert first is MessageRoute.IGNORE
    assert second is MessageRoute.IGNORE
    assert client.calls == 2


def test_an_empty_root_lookup_stays_silent_but_is_not_cached() -> None:
    """Read-after-write lag is uncertainty, not permanent foreign ownership."""
    client = RootClient()
    ownership = ThreadOwnership()

    assert (
        ownership.route(
            _event(parent_user_id="UCHASE"),
            client,
            bot_user_id=GABLE_USER,
            bot_id=GABLE_BOT,
        )
        is MessageRoute.IGNORE
    )
    client.roots["1786.1"] = {"user": "UCHASE", "text": "<@UGABLE> help"}
    assert (
        ownership.route(
            _event(parent_user_id="UCHASE", ts="1786.3"),
            client,
            bot_user_id=GABLE_USER,
            bot_id=GABLE_BOT,
        )
        is MessageRoute.THREAD_REPLY
    )
    assert client.calls == [(CHANNEL, "1786.1", 1), (CHANNEL, "1786.1", 1)]


def test_a_received_root_mention_is_owned_before_read_api_catches_up() -> None:
    """The authoritative app mention prevents a first-reply ownership race."""
    ownership = ThreadOwnership()
    ownership.remember_owned(CHANNEL, "1786.1")

    route = ownership.route(
        _event(parent_user_id="UCHASE"),
        RootClient(),
        bot_user_id=GABLE_USER,
        bot_id=GABLE_BOT,
    )

    assert route is MessageRoute.THREAD_REPLY


def test_a_retried_slack_event_is_accepted_exactly_once_under_concurrency() -> None:
    """A lost acknowledgement cannot run the same edit or photo twice."""
    guard = EventReplayGuard()
    event = _event(event_ts="1786.2")

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(
            pool.map(lambda _index: guard.first_delivery(event, route="message"), range(8))
        )

    assert accepted.count(True) == 1
    assert accepted.count(False) == 7


def test_unidentifiable_events_are_not_silently_dropped_as_replays() -> None:
    """Failing open on replay identity preserves a real human request."""
    guard = EventReplayGuard()
    event = {"channel": CHANNEL, "user": "UCHASE"}

    assert guard.first_delivery(event, route="message")
    assert guard.first_delivery(event, route="message")


def test_cache_size_must_be_positive() -> None:
    """A configuration error cannot silently create an unbounded cache."""
    with pytest.raises(ValueError, match="must be positive"):
        ThreadOwnership(max_entries=0)


def test_explicit_gable_mention_still_answers_inside_a_foreign_thread() -> None:
    """The direct-mention path intentionally bypasses root ownership."""
    said: list[dict[str, Any]] = []
    asked: list[str] = []

    class MentionClient:
        """Support the name lookup and native waiting surface."""

        def users_info(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            """Return Chase's name."""
            return {"user": {"profile": {"first_name": "Chase"}}}

        def assistant_threads_setStatus(self, **_kwargs: Any) -> None:  # noqa: ANN401, N802
            """Accept the native status call."""

    def thinker(message: str, speaker: str = "") -> Decision:
        """Record what reached the conversation model."""
        assert speaker == "Chase"
        asked.append(message)
        return Decision(reply="Tell me which flyer you mean.")

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        """Record the direct-mention reply."""
        said.append(kwargs)
        return {"ts": "1786.3"}

    answer_mention(
        _event(
            thread_ts="1786.1",
            text="<@UGABLE> Number 13 and 22",
            parent_user_id="UMONARCH",
        ),
        say,
        MentionClient(),
        thinker,
    )

    assert asked == ["Number 13 and 22"]
    assert said == [{"text": "Tell me which flyer you mean.", "thread_ts": "1786.1"}]


def test_a_photo_attached_to_an_app_mention_uses_the_photo_workflow() -> None:
    """Mentioning Gable with the requested photo cannot turn it into prose."""
    handled: list[str] = []
    thought: list[str] = []
    said: list[dict[str, Any]] = []

    class MentionClient:
        """Accept the native status calls used by the file workflow."""

        def assistant_threads_setStatus(self, **_kwargs: Any) -> None:  # noqa: ANN401, N802
            """Stand in for Slack's native status method."""

    def photo_handler(
        event: dict[str, Any],
        _client: Any,  # noqa: ANN401
        _progress: Any,  # noqa: ANN401
    ) -> str:
        handled.append(str(event["files"][0]["id"]))
        return "I fitted the photo and finished the flyer."

    def thinker(message: str, speaker: str = "") -> Decision:
        del speaker
        thought.append(message)
        return Decision(reply="This should not be called.")

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        said.append(kwargs)
        return {"ts": "said"}

    process_mention(
        _event(
            type="app_mention",
            text="<@UGABLE> here it is",
            files=[{"id": "F1"}],
        ),
        say,
        MentionClient(),
        thinker,
        photo_handler,
    )

    assert handled == ["F1"]
    assert thought == []
    assert said == [{"text": "I fitted the photo and finished the flyer.", "thread_ts": "1786.1"}]


def test_app_mention_and_message_delivery_of_one_photo_run_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlapping Slack subscriptions cannot duplicate one file handoff."""
    import slack_bolt

    class FakeBoltApp:
        """Capture event decorators without constructing a real Slack client."""

        def __init__(self, **_kwargs: Any) -> None:  # noqa: ANN401
            self.handlers: dict[str, Any] = {}

        def event(self, name: str) -> Any:  # noqa: ANN401
            def register(handler: Any) -> Any:  # noqa: ANN401
                self.handlers[name] = handler
                return handler

            return register

    class EventClient:
        """Support native status; ownership uses the event's parent user id."""

        def assistant_threads_setStatus(self, **_kwargs: Any) -> None:  # noqa: ANN401, N802
            """Stand in for native status."""

    monkeypatch.setattr(slack_bolt, "App", FakeBoltApp)
    handled: list[str] = []
    thought: list[str] = []

    def photo_handler(
        event: dict[str, Any],
        _client: Any,  # noqa: ANN401
        _progress: Any,  # noqa: ANN401
    ) -> str:
        handled.append(str(event["files"][0]["id"]))
        return "I fitted the photo and finished the flyer."

    def thinker(message: str, speaker: str = "") -> Decision:
        del speaker
        thought.append(message)
        return Decision(reply="This should not be called.")

    said: list[dict[str, Any]] = []

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        said.append(kwargs)
        return {"ts": "said"}

    app = build_app(
        "xoxb-test-token",
        file_share_handler=photo_handler,
        allowed_channel=CHANNEL,
        allowed_user_ids=frozenset(("UCHASE",)),
        thinker=thinker,
    )
    event = _event(
        type="app_mention",
        event_ts="1786.2",
        parent_user_id=GABLE_USER,
        text="<@UGABLE> here it is",
        files=[{"id": "F1"}],
    )
    app.handlers["app_mention"](event, say, EventClient())
    app.handlers["message"](
        {**event, "type": "message"},
        say,
        EventClient(),
        {"bot_user_id": GABLE_USER, "bot_id": GABLE_BOT},
    )

    assert handled == ["F1"]
    assert thought == []
    assert len(said) == 1


def _top_level(text: str, **overrides: object) -> dict[str, Any]:
    """Build one human message posted straight into the channel."""
    event: dict[str, Any] = {
        "channel": CHANNEL,
        "user": "UCARMEN",
        "ts": "1786918349.613549",
        "text": text,
    }
    event.update(overrides)
    return event


def test_a_top_level_channel_message_without_a_mention_stays_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gable answers when called, not when discussed.

    Chase, 2026-08-17: it must not reply to "Hey Carmen", to an @carmen mention,
    or to "Gable will build this one" — that last one is Chase telling Carmen
    something ABOUT Gable, and an earlier version of this answered it. In the
    channel a real @Gable mention is required; inside a thread Gable owns, no
    mention is needed at all.
    """
    import slack_bolt

    class FakeBoltApp:
        """Capture event decorators without constructing a real Slack client."""

        def __init__(self, **_kwargs: Any) -> None:  # noqa: ANN401
            self.handlers: dict[str, Any] = {}

        def event(self, name: str) -> Any:  # noqa: ANN401
            def register(handler: Any) -> Any:  # noqa: ANN401
                self.handlers[name] = handler
                return handler

            return register

    monkeypatch.setattr(slack_bolt, "App", FakeBoltApp)
    thought: list[str] = []

    def thinker(message: str, speaker: str = "") -> Decision:
        del speaker
        thought.append(message)
        return Decision(reply="should not be reached")

    said: list[dict[str, Any]] = []

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        said.append(kwargs)
        return {"ts": "said"}

    app = build_app(
        "xoxb-test-token",
        allowed_channel=CHANNEL,
        allowed_user_ids=frozenset(("UCARMEN",)),
        thinker=thinker,
    )
    for text in (
        "Hey Carmen",
        "@carmen thoughts on this one?",
        "Gable will build this one",
        "I think gable already did that",
        "Thanks Gable!",
        "sounds good, thanks!",
    ):
        app.handlers["message"](
            _top_level(text, type="message", event_ts=f"1786918349.{len(text)}"),
            say,
            object(),
            {"bot_user_id": GABLE_USER, "bot_id": GABLE_BOT},
        )

    assert thought == [], "Gable answered a message that did not call it"
    assert said == []


def test_a_thread_reply_needs_no_mention(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of Chase's rule: inside a thread, no at-sign.

    A mention is how Gable is called in the channel. Once a thread is open it is
    already a conversation with Gable, and making Carmen type @Gable on every
    reply would be its own round-trip tax.
    """
    import slack_bolt

    class FakeBoltApp:
        """Capture event decorators without constructing a real Slack client."""

        def __init__(self, **_kwargs: Any) -> None:  # noqa: ANN401
            self.handlers: dict[str, Any] = {}

        def event(self, name: str) -> Any:  # noqa: ANN401
            def register(handler: Any) -> Any:  # noqa: ANN401
                self.handlers[name] = handler
                return handler

            return register

    class EventClient:
        """Support the native waiting status only."""

        def assistant_threads_setStatus(self, **_kwargs: Any) -> None:  # noqa: ANN401, N802
            """Stand in for native status."""

    monkeypatch.setattr(slack_bolt, "App", FakeBoltApp)
    thought: list[str] = []

    def thinker(message: str, speaker: str = "") -> Decision:
        del speaker
        thought.append(message)
        return Decision(reply="On it.")

    said: list[dict[str, Any]] = []

    def say(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        said.append(kwargs)
        return {"ts": "said"}

    app = build_app(
        "xoxb-test-token",
        allowed_channel=CHANNEL,
        allowed_user_ids=frozenset(("UCARMEN",)),
        thinker=thinker,
        history_provider=None,
    )
    # parent_user_id is Gable, which is authoritative ownership of the root.
    app.handlers["message"](
        _event(
            user="UCARMEN",
            text="can you make the price bigger?",
            parent_user_id=GABLE_USER,
        ),
        say,
        EventClient(),
        {"bot_user_id": GABLE_USER, "bot_id": GABLE_BOT},
    )

    assert thought == ["can you make the price bigger?"]
    assert said and said[0]["text"] == "On it."
