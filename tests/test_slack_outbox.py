"""Slack history reconciliation proves acknowledgement loss conservatively."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gable.pipeline.questions import ReconcileState
from gable.slackapp.outbox import (
    SlackIdentity,
    SlackOutboxReconciler,
    notification_block_id,
)

CHANNEL = "C0B02721MNK"
ROOT = "1786586400.000100"
CREATED = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
NOTIFICATION_ID = "8d0f94b5-b940-4613-9901-9c6c03ae56e8"


class SlackHistory:
    """Configurable documented Slack history/auth surface."""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        *,
        pages: list[dict[str, Any]] | None = None,
        has_more: bool = False,
        cursor: str = "",
        explode: bool = False,
    ) -> None:
        """Store one fake page and its failure/pagination behavior."""
        self.messages = messages
        self.pages = pages
        self.has_more = has_more
        self.cursor = cursor
        self.explode = explode
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.auth_calls = 0

    def auth_test(self) -> dict[str, str]:
        """Return the documented Gable bot identity."""
        self.auth_calls += 1
        return {"user_id": "UGABLE", "bot_id": "BGABLE", "app_id": "AGABLE"}

    def conversations_history(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        """Record and return one root-history page."""
        self.calls.append(("history", kwargs))
        if self.explode:
            raise RuntimeError("Slack unavailable")
        return self._response()

    def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        """Record and return one thread-reply page."""
        self.calls.append(("replies", kwargs))
        if self.explode:
            raise RuntimeError("Slack unavailable")
        return self._response()

    def _response(self) -> dict[str, Any]:
        if self.pages is not None:
            index = min(len(self.calls) - 1, len(self.pages) - 1)
            return self.pages[index]
        return {
            "messages": self.messages,
            "has_more": self.has_more,
            "response_metadata": {"next_cursor": self.cursor},
        }


def _ts(offset: float = 0.0) -> str:
    return f"{datetime.now(UTC).timestamp() + offset:.6f}"


def _message(
    text: str,
    *,
    thread_ts: str = "",
    notification_id: str = NOTIFICATION_ID,
    **identity: str,
) -> dict[str, Any]:
    return {
        "ts": _ts(),
        "text": text,
        "thread_ts": thread_ts,
        "user": "UGABLE",
        "bot_id": "BGABLE",
        "app_id": "AGABLE",
        "blocks": [
            {
                "type": "section",
                "block_id": notification_block_id(notification_id),
                "text": {"type": "mrkdwn", "text": text},
            }
        ],
        **identity,
    }


def test_unique_root_match_recovers_after_acknowledgement_loss() -> None:
    text = "Your flyer is ready. <https://slides.test/deck|Open the flyer>"
    client = SlackHistory([_message(text)])
    reconcile = SlackOutboxReconciler(client, CHANNEL)

    timestamp = reconcile(text, None, CREATED, NOTIFICATION_ID)

    assert timestamp.state is ReconcileState.FOUND
    assert timestamp.timestamp == client.messages[0]["ts"]
    method, arguments = client.calls[0]
    assert method == "history"
    assert arguments["channel"] == CHANNEL
    assert arguments["inclusive"] is True
    assert arguments["oldest"] < arguments["latest"]
    assert client.auth_calls == 1


#: The exact pair observed on 2026-08-15. The left string is what the outbox
#: stored; the right is what ``conversations.history`` returned for the very
#: message Slack had accepted. They differ only in the line break.
STORED_WITH_A_LINE_BREAK = (
    "I rendered it, but the word “Today.” runs below the selling callout box "
    "and overlaps the footer bar.\nI have not sent it as finished."
)
RETURNED_BY_SLACK = (
    "I rendered it, but the word “Today.” runs below the selling callout box "
    "and overlaps the footer bar. I have not sent it as finished."
)


def test_a_line_break_slack_returned_as_a_space_still_reconciles() -> None:
    """The defect that kept one real row pending for a day, logging every minute.

    Reconciliation answers FOUND, ABSENT or UNKNOWN, and only UNKNOWN has no way
    out: it neither confirms nor permits a repost. A message Slack had plainly
    accepted landed there because the stored text kept a newline the returned
    text did not.
    """
    client = SlackHistory([_message(RETURNED_BY_SLACK)])

    result = SlackOutboxReconciler(client, CHANNEL)(
        STORED_WITH_A_LINE_BREAK,
        None,
        CREATED,
        NOTIFICATION_ID,
    )

    assert result.state is ReconcileState.FOUND
    assert result.timestamp == client.messages[0]["ts"]


def test_paragraph_breaks_reconcile_the_same_way() -> None:
    """Every message carries these now, so every message depended on this."""
    stored = "I resized and fitted the photo.\n\nNobody gave me the price."
    client = SlackHistory([_message("I resized and fitted the photo. Nobody gave me the price.")])

    result = SlackOutboxReconciler(client, CHANNEL)(stored, None, CREATED, NOTIFICATION_ID)

    assert result.state is ReconcileState.FOUND


def test_collapsing_whitespace_does_not_make_two_outcomes_interchangeable() -> None:
    """Text still has to say the same thing; only its spacing is forgiven."""
    client = SlackHistory([_message("I rendered it, but I have not sent it as finished.")])

    result = SlackOutboxReconciler(client, CHANNEL)(
        "I rendered it and sent it as finished.",
        None,
        CREATED,
        NOTIFICATION_ID,
    )

    assert result.state is ReconcileState.UNKNOWN


def test_same_visible_link_label_with_a_different_target_stays_pending() -> None:
    text = "Your flyer is ready. <https://slides.test/right|Open the flyer>"
    wrong = "Your flyer is ready. <https://slides.test/wrong|Open the flyer>"
    client = SlackHistory([_message(wrong)])

    assert (
        SlackOutboxReconciler(client, CHANNEL)(text, None, CREATED, NOTIFICATION_ID).state
        is ReconcileState.UNKNOWN
    )


def test_identical_root_text_without_the_exact_block_id_is_not_a_match() -> None:
    """A nearby older headline cannot be mistaken for this outbox identity."""
    text = "New Sold request from Carmen — 1 Main St"
    older = _message(text, notification_id="different-notification-id")
    result = SlackOutboxReconciler(SlackHistory([older]), CHANNEL)(
        text,
        None,
        CREATED,
        NOTIFICATION_ID,
    )

    assert result.state is ReconcileState.ABSENT


def test_exact_block_id_with_conflicting_client_id_fails_closed() -> None:
    text = "New Sold request from Carmen — 1 Main St"
    conflicting = _message(text)
    conflicting["client_msg_id"] = "different-client-id"
    result = SlackOutboxReconciler(SlackHistory([conflicting]), CHANNEL)(
        text,
        None,
        CREATED,
        NOTIFICATION_ID,
    )

    assert result.state is ReconcileState.UNKNOWN


def test_unique_thread_reply_requires_the_exact_root() -> None:
    text = "Can you send me the image?"
    reply = _message(text, thread_ts=ROOT)
    client = SlackHistory([reply])
    reconcile = SlackOutboxReconciler(
        client,
        CHANNEL,
        identity=SlackIdentity(user_id="UGABLE", bot_id="BGABLE"),
    )

    result = reconcile(text, ROOT, CREATED, NOTIFICATION_ID)
    assert result.state is ReconcileState.FOUND
    assert result.timestamp == reply["ts"]
    method, arguments = client.calls[0]
    assert method == "replies"
    assert arguments["channel"] == CHANNEL
    assert arguments["ts"] == ROOT
    assert client.auth_calls == 0


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [_message("Different visible text")],
        [_message("Can you send me the image?", thread_ts="wrong-root")],
        [_message("Can you send me the image?", user="UOTHER", bot_id="BOTHER")],
    ],
)
def test_none_wrong_text_wrong_thread_or_wrong_author_stays_pending(
    messages: list[dict[str, Any]],
) -> None:
    client = SlackHistory(messages)
    reconcile = SlackOutboxReconciler(client, CHANNEL)

    result = reconcile("Can you send me the image?", ROOT, CREATED, NOTIFICATION_ID)
    assert result.state in {ReconcileState.ABSENT, ReconcileState.UNKNOWN}


def test_multiple_exact_matches_are_ambiguous() -> None:
    text = "Your flyer is ready. Open the flyer"
    client = SlackHistory([_message(text), _message(text)])

    assert (
        SlackOutboxReconciler(client, CHANNEL)(text, None, CREATED, NOTIFICATION_ID).state
        is ReconcileState.UNKNOWN
    )


def test_cursor_pagination_reads_the_complete_window_before_matching() -> None:
    text = "Your flyer is ready. Open the flyer"
    exact = _message(text)
    client = SlackHistory(
        [],
        pages=[
            {
                "messages": [_message("A different notification", notification_id="different-id")],
                "has_more": True,
                "response_metadata": {"next_cursor": "page-two"},
            },
            {
                "messages": [exact],
                "has_more": False,
                "response_metadata": {"next_cursor": ""},
            },
        ],
    )

    result = SlackOutboxReconciler(client, CHANNEL)(text, None, CREATED, NOTIFICATION_ID)

    assert result.state is ReconcileState.FOUND
    assert result.timestamp == exact["ts"]
    assert client.calls[1][1]["cursor"] == "page-two"


def test_cursor_pagination_can_prove_complete_absence() -> None:
    client = SlackHistory(
        [],
        pages=[
            {
                "messages": [_message("Different one", notification_id="different-one")],
                "has_more": True,
                "response_metadata": {"next_cursor": "page-two"},
            },
            {
                "messages": [_message("Different two", notification_id="different-two")],
                "has_more": False,
                "response_metadata": {"next_cursor": ""},
            },
        ],
    )

    result = SlackOutboxReconciler(client, CHANNEL)(
        "Wanted notification",
        None,
        CREATED,
        NOTIFICATION_ID,
    )

    assert result.state is ReconcileState.ABSENT
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    ("explode", "has_more", "cursor"),
    [(True, False, ""), (False, True, ""), (False, False, "another-page")],
)
def test_api_failure_or_unread_history_stays_pending(
    explode: bool,
    has_more: bool,
    cursor: str,
) -> None:
    text = "Your flyer is ready. Open the flyer"
    client = SlackHistory(
        [_message(text)],
        explode=explode,
        has_more=has_more,
        cursor=cursor,
    )

    assert (
        SlackOutboxReconciler(client, CHANNEL)(text, None, CREATED, NOTIFICATION_ID).state
        is ReconcileState.UNKNOWN
    )


def test_outside_creation_window_and_missing_auth_identity_stay_pending() -> None:
    text = "Your flyer is ready. Open the flyer"
    too_old = _message(text)
    too_old["ts"] = f"{(datetime.now(UTC) - timedelta(days=1)).timestamp():.6f}"
    old_client = SlackHistory([too_old])
    assert (
        SlackOutboxReconciler(old_client, CHANNEL)(text, None, CREATED, NOTIFICATION_ID).state
        is ReconcileState.UNKNOWN
    )

    unknown = SlackHistory([_message(text)])
    unknown.auth_test = lambda: {}  # type: ignore[method-assign]
    assert (
        SlackOutboxReconciler(unknown, CHANNEL)(text, None, CREATED, NOTIFICATION_ID).state
        is ReconcileState.UNKNOWN
    )


def test_transient_auth_failure_does_not_disable_later_reconciliation() -> None:
    """A failed identity read leaves no unsafe or permanently poisoned cache."""
    text = "Your flyer is ready. Open the flyer"
    client = SlackHistory([_message(text)])
    attempts = 0

    def transient_auth() -> dict[str, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Slack unavailable")
        return {"user_id": "UGABLE", "bot_id": "BGABLE"}

    client.auth_test = transient_auth  # type: ignore[method-assign]
    reconcile = SlackOutboxReconciler(client, CHANNEL)

    assert reconcile(text, None, CREATED, NOTIFICATION_ID).state is ReconcileState.UNKNOWN
    assert reconcile(text, None, CREATED, NOTIFICATION_ID).timestamp == client.messages[0]["ts"]
    assert attempts == 2
