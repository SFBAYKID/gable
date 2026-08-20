"""Conversation turns receive bounded thread history and persisted listing facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.slackapp.app import answer_thread_reply
from gable.slackapp.brain import Decision
from gable.slackapp.context import (
    MAX_HISTORY_TURNS,
    decide_with_context,
    listing_context,
    thread_history,
)

CHANNEL = "C0B02721MNK"
THREAD = "1786605927.301519"


class HistoryClient:
    """Return one configurable page of Slack thread messages."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        """Store the messages and start an empty call ledger."""
        self.messages = messages
        self.calls: list[dict[str, Any]] = []

    def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        """Record the documented read and return the configured thread."""
        self.calls.append(kwargs)
        return {"messages": self.messages, "response_metadata": {"next_cursor": ""}}


def _event(**overrides: object) -> dict[str, Any]:
    """Build one current owned-thread reply."""
    event: dict[str, Any] = {
        "channel": CHANNEL,
        "thread_ts": THREAD,
        "ts": "1786606000.400000",
        "user": "UCHASE",
        "text": "the big one",
    }
    event.update(overrides)
    return event


def _intake() -> Intake:
    """Return one listing with enough identifying context for Slack."""
    return Intake(
        agent_email="mike@cornerhouserealty.com",
        agent_name="Mike Kulnich",
        request_type="Sold",
        address="703 Perception Way, Aberdeen, MD 21001",
        post_details="",
        open_house="",
        new_price="",
        closing_price="615000",
        extra_notes="",
        side="Seller",
        notes="",
    )


def test_thread_history_preserves_the_question_that_a_terse_reply_answers() -> None:
    client = HistoryClient(
        [
            {
                "ts": THREAD,
                "bot_id": "BGABLE",
                "text": "Your flyer is ready. Open the flyer.",
            },
            {
                "ts": "1786605990.100000",
                "user": "UCHASE",
                "text": "update the image",
            },
            {
                "ts": "1786605995.200000",
                "bot_id": "BGABLE",
                "text": "Did you mean the large photo or Mike's headshot?",
            },
            {
                "ts": "1786606000.400000",
                "user": "UCHASE",
                "text": "the big one",
            },
        ]
    )

    history = thread_history(_event(), client)

    assert history[-2:] == [
        ("user", "update the image"),
        ("Gable", "Did you mean the large photo or Mike's headshot?"),
    ]
    assert ("user", "the big one") not in history
    assert client.calls == [
        {
            "channel": CHANNEL,
            "ts": THREAD,
            "latest": "1786606000.400000",
            "inclusive": False,
            "limit": 200,
        }
    ]


def test_thread_history_keeps_only_the_bounded_recent_window() -> None:
    messages = [
        {"ts": str(index), "user": "UCHASE", "text": f"turn {index}"}
        for index in range(MAX_HISTORY_TURNS + 5)
    ]

    history = thread_history(_event(ts="999"), HistoryClient(messages))

    assert len(history) == MAX_HISTORY_TURNS
    assert history[0] == ("user", "turn 5")


def test_top_level_mention_has_no_prior_thread_read() -> None:
    client = HistoryClient([])

    assert thread_history(_event(thread_ts=""), client) == []
    assert client.calls == []


def test_thread_history_reads_every_page_before_taking_the_recent_tail() -> None:
    class PagedClient:
        """Return two documented cursor pages."""

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            """Select the next page from Slack's cursor argument."""
            self.calls.append(kwargs)
            if kwargs.get("cursor") == "page-two":
                return {
                    "messages": [
                        {
                            "ts": "1786605995.200000",
                            "bot_id": "BGABLE",
                            "text": "Did you mean the large photo or the headshot?",
                        }
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            return {
                "messages": [
                    {"ts": THREAD, "bot_id": "BGABLE", "text": "Your flyer is ready."},
                    {"ts": "1786605990.100000", "user": "UCHASE", "text": "update the image"},
                ],
                "response_metadata": {"next_cursor": "page-two"},
            }

    client = PagedClient()

    history = thread_history(_event(), client)

    assert history[-2:] == [
        ("user", "update the image"),
        ("Gable", "Did you mean the large photo or the headshot?"),
    ]
    assert client.calls[1]["cursor"] == "page-two"


def test_thread_history_excludes_other_people_and_apps_from_action_context() -> None:
    client = HistoryClient(
        [
            {"ts": THREAD, "user": "UGABLE", "bot_id": "BGABLE", "text": "Flyer ready."},
            {"ts": "2", "user": "UOTHER", "text": "replace the image"},
            {
                "ts": "3",
                "user": "UMONARCH",
                "bot_id": "BMONARCH",
                "text": "Did you mean the large photo or the headshot?",
            },
            {"ts": "4", "user": "UCHASE", "text": "update the image"},
            {
                "ts": "5",
                "user": "UGABLE",
                "bot_id": "BGABLE",
                "text": "Did you mean the large photo or the headshot?",
            },
        ]
    )

    history = thread_history(
        _event(ts="9"),
        client,
        allowed_user_ids=frozenset({"UCHASE", "UCARMEN"}),
        bot_user_id="UGABLE",
        bot_id="BGABLE",
    )

    assert history == [
        ("Gable", "Flyer ready."),
        ("user", "update the image"),
        ("Gable", "Did you mean the large photo or the headshot?"),
    ]


def test_listing_context_names_the_owned_run_and_what_it_is_waiting_for(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    assert store.record_submission(
        connection,
        "response-1",
        48,
        "today",
        _intake(),
        "hash",
        "Testing_1",
    )
    run = store.start_run(connection, "response-1")
    store.set_status(
        connection,
        run.run_id,
        "needs_photo",
        "waiting for the supplied property image",
        template_label="Sold",
        slack_thread_ts=THREAD,
        failure_reason="Can you send me the image?",
    )

    context = listing_context(connection, THREAD)

    assert "Run status: needs_photo." in context
    assert "Request type: Sold." in context
    assert "703 Perception Way, Aberdeen, MD 21001" in context
    assert "Submitting agent: Mike Kulnich." in context
    assert "Selected template: Sold." in context
    assert "No flyer has been built in this thread yet." in context
    assert "This run has no hero photo yet." in context
    assert "Can you send me the image?" in context
    connection.close()


def test_terse_clarification_reaches_the_thinker_with_prior_question_and_listing() -> None:
    seen: dict[str, Any] = {}

    def thinker(
        message: str,
        *,
        history: list[tuple[str, str]],
        context: str,
        speaker: str,
    ) -> Decision:
        """Record the exact context sent to the model seam."""
        seen.update(
            message=message,
            history=history,
            context=context,
            speaker=speaker,
        )
        return Decision(
            reply="Replacing the hero photo.",
            tool="replace_photo",
            arguments={"which": "hero"},
        )

    decision = decide_with_context(
        thinker,
        "the big one",
        "Chase",
        _event(),
        object(),
        lambda _event, _client: [
            ("user", "update the image"),
            ("Gable", "Did you mean the large photo or the headshot?"),
        ],
        lambda _thread: "Property address: 703 Perception Way. A flyer exists in this thread.",
    )

    assert decision.arguments == {"which": "hero"}
    assert seen == {
        "message": "the big one",
        "history": [
            ("user", "update the image"),
            ("Gable", "Did you mean the large photo or the headshot?"),
        ],
        "context": "Property address: 703 Perception Way. A flyer exists in this thread.",
        "speaker": "Chase",
    }


def test_owned_thread_handler_supplies_history_and_listing_before_answering() -> None:
    seen: dict[str, Any] = {}
    said: list[dict[str, Any]] = []

    class Client:
        """Provide the speaker profile and native waiting method."""

        def users_info(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            """Return Chase's first name."""
            return {"user": {"profile": {"first_name": "Chase"}}}

        def assistant_threads_setStatus(self, **_kwargs: Any) -> None:  # noqa: ANN401, N802
            """Accept native waiting updates."""

    def thinker(
        message: str,
        *,
        history: list[tuple[str, str]],
        context: str,
        speaker: str,
    ) -> Decision:
        """Record the full production-shaped model call."""
        seen.update(
            message=message,
            history=history,
            context=context,
            speaker=speaker,
        )
        return Decision(reply="Send me the new property photo.")

    answer_thread_reply(
        _event(),
        lambda **kwargs: said.append(kwargs),
        Client(),
        thinker,
        history_provider=lambda _event, _client: [
            ("user", "update the image"),
            ("Gable", "Did you mean the large photo or the headshot?"),
        ],
        context_provider=lambda _thread: "Property address: 703 Perception Way.",
    )

    assert seen["message"] == "the big one"
    assert seen["history"][-1][1].endswith("headshot?")
    assert seen["context"] == "Property address: 703 Perception Way."
    assert seen["speaker"] == "Chase"
    assert said == [{"text": "Send me the new property photo.", "thread_ts": THREAD}]


def test_a_context_read_failure_does_not_drop_the_human_reply() -> None:
    def fail_history(
        _event: dict[str, Any],
        _client: Any,  # noqa: ANN401 - untyped Slack provider seam
    ) -> list[tuple[str, str]]:
        """Simulate a Slack read failure outside the default provider."""
        raise RuntimeError("fixed history failure")

    def fail_context(_thread: str) -> str:
        """Simulate a database read failure outside the runtime wrapper."""
        raise RuntimeError("fixed context failure")

    def thinker(message: str, speaker: str = "") -> Decision:
        """Return a useful answer even when optional context is unavailable."""
        assert message == "what do you need?"
        assert speaker == "Chase"
        return Decision(reply="Send me the property image.")

    decision = decide_with_context(
        thinker,
        "what do you need?",
        "Chase",
        _event(),
        object(),
        fail_history,
        fail_context,
    )

    assert decision.reply == "Send me the property image."


def _thread_run(connection: Any, status: str) -> str:  # noqa: ANN401
    """A recorded submission with one run owning THREAD, in the given state."""
    assert store.record_submission(
        connection, "response-ctx", 49, "today", _intake(), "hash-ctx", "Testing_1"
    )
    run = store.start_run(connection, "response-ctx")
    store.set_status(
        connection, run.run_id, status, "waiting", template_label="Sold", slack_thread_ts=THREAD
    )
    return str(run.run_id)


def test_the_model_is_told_when_a_run_is_owed_its_photo(tmp_path: Path) -> None:
    """Status names the work only a person can do, not everything outstanding.

    A run owed a widened design AND its photograph parks in `needs_template`,
    so the reply shortcuts that read only the status could not tell a photo was
    still wanted.
    """
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    run_id = _thread_run(connection, status="needs_template")
    store.set_awaiting_photo(connection, run_id, True)

    context = listing_context(connection, THREAD)

    assert "Run status: needs_template." in context
    assert "asked for the property photo and is waiting for it" in context
    connection.close()


def test_a_refused_photo_is_not_described_as_attached(tmp_path: Path) -> None:
    """`photo_url` survives a refusal as evidence, not as a usable photograph."""
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    run_id = _thread_run(connection, status="needs_photo")
    store.set_status(
        connection,
        run_id,
        "needs_photo",
        "the supplied photo shows a house number that conflicts with the address",
        photo_url="http://images.example/wrong-house.jpg",
        slack_thread_ts=THREAD,
    )

    context = listing_context(connection, THREAD)

    assert "a check refused" in context
    assert "A human-supplied hero photo is attached" not in context
    connection.close()
