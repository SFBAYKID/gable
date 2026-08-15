"""Prove whether Slack accepted an outbox post whose acknowledgement was lost.

Slack and SQLite cannot commit together.  The durable outbox normally closes
that gap with a stable ``client_msg_id``, but Slack's public method reference
does not promise that a repeated call will return the original timestamp.  This
module therefore uses the documented history APIs as a conservative second
proof: one Gable-authored message, with the exact visible text and exact
root/thread position, inside the outbox's bounded time window.

No match or any ambiguity returns an empty timestamp and leaves SQLite pending.
"""

from __future__ import annotations

import html
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from gable.pipeline.questions import ReconcileState, Reconciliation

logger = logging.getLogger("gable.slack.outbox")

# Both methods document cursor pagination and recommend no more than 200 rows.
# Read every page in the bounded creation-to-now window before proving absence
# or uniqueness. The page cap bounds one worker pass; reaching it is UNKNOWN,
# never a partial-history match or false absence proof.
RECONCILIATION_LIMIT: Final[int] = 100
MAX_RECONCILIATION_PAGES: Final[int] = 10
_CLOCK_SKEW_SECONDS: Final[float] = 5.0
_DESCRIPTIVE_CONTACT_LINK: Final[re.Pattern[str]] = re.compile(
    r"<((?:tel|mailto):[^>|]*)\|([^>]*)>"
)
_CONTACT_AUTOLINK: Final[re.Pattern[str]] = re.compile(r"<((?:tel|mailto):[^>|]*)>")


@dataclass(frozen=True, slots=True)
class SlackIdentity:
    """Documented bot identity fields returned by ``auth.test``."""

    user_id: str = ""
    bot_id: str = ""
    app_id: str = ""

    @property
    def known(self) -> bool:
        """Whether at least one returned message field can identify Gable."""
        return bool(self.user_id or self.bot_id or self.app_id)


def canonical_visible_text(text: str) -> str:
    r"""Canonicalize only Slack transformations that preserve the full message.

    HTTP(S) destinations remain part of the proof: two flyers whose visible
    label is ``Open the flyer`` are not the same outcome. Slack may auto-wrap a
    bare telephone number or email address; that wrapper is removed only when
    its label is exactly the underlying value.

    **Every run of whitespace collapses to one space, line breaks included.**
    This used to keep each line intact, on the reasoning that a line break is
    part of the message. Slack disagrees: a run outcome posted on 2026-08-14
    came back from ``conversations.history`` with its ``\n`` rendered as a
    single space, so the stored text and the accepted text could never compare
    equal. The row stayed pending for over a day, logging an error a minute,
    and no later pass could ever resolve it — reconciliation returned UNKNOWN
    rather than FOUND or ABSENT, which is the one verdict with no way out.
    That defect reaches every message now that Gable writes in paragraphs.

    Collapsing costs nothing that matters. The notification id carried in the
    message's own block is what identifies it; this text check only corroborates
    that the accepted message says what the outbox meant to say, and two
    outcomes that differ solely in where a line wraps are the same outcome.
    """
    normalized = html.unescape(text or "").replace("\r\n", "\n").replace("\r", "\n")

    def unwrap_contact(match: re.Match[str]) -> str:
        target, label = match.groups()
        _scheme, _separator, value = target.partition(":")
        return label if label == value else match.group(0)

    normalized = _DESCRIPTIVE_CONTACT_LINK.sub(unwrap_contact, normalized)
    normalized = _CONTACT_AUTOLINK.sub(r"\1", normalized)
    # str.split() with no argument splits on every whitespace run, so tabs and
    # the paragraph breaks voice.paragraphs writes normalise the same way.
    return " ".join(normalized.split())


class SlackOutboxReconciler:
    """Find one uniquely matching accepted message in Gable's configured channel."""

    def __init__(
        self,
        client: Any,  # noqa: ANN401 - Slack WebClient is untyped upstream
        channel: str,
        *,
        identity: SlackIdentity | None = None,
    ) -> None:
        """Bind the Slack read client and configured, never caller-supplied channel."""
        self.client = client
        self.channel = channel
        self._identity = identity if identity is not None and identity.known else None
        self._identity_checked = self._identity is not None
        self._identity_lock = threading.Lock()

    def __call__(
        self,
        text: str,
        thread_ts: str | None,
        created_at: str,
        notification_id: str,
    ) -> Reconciliation:
        """Return found, proved absent, or unknown without conflating them."""
        if not self.channel or not text.strip() or not notification_id.strip():
            return Reconciliation(ReconcileState.UNKNOWN)
        try:
            identity = self._gable_identity()
            oldest = _oldest(created_at)
            if identity is None or not oldest:
                return Reconciliation(ReconcileState.UNKNOWN)
            latest = f"{datetime.now(UTC).timestamp() + _CLOCK_SKEW_SECONDS:.6f}"
            arguments: dict[str, Any] = {
                "channel": self.channel,
                "oldest": oldest,
                "latest": latest,
                "inclusive": True,
                "limit": RECONCILIATION_LIMIT,
            }
            root = (thread_ts or "").strip()
            messages = self._complete_history(arguments, root)
            if messages is None:
                return Reconciliation(ReconcileState.UNKNOWN)
            wanted = canonical_visible_text(text)
            marked = [
                message
                for message in messages
                if isinstance(message, dict) and _has_notification_block(message, notification_id)
            ]
            if not marked:
                return Reconciliation(ReconcileState.ABSENT)
            matches = [
                str(message.get("ts") or "").strip()
                for message in marked
                if _candidate_matches(
                    message,
                    wanted,
                    root,
                    identity,
                    oldest,
                    latest,
                    notification_id,
                )
            ]
            if len(marked) == 1 and len(matches) == 1:
                return Reconciliation(ReconcileState.FOUND, matches[0])
            return Reconciliation(ReconcileState.UNKNOWN)
        except Exception:
            # Slack SDK exceptions can contain response bodies. Keep logs generic
            # and leave the durable row pending for a later pass.
            logger.warning("could not reconcile an unconfirmed Slack notification")
            return Reconciliation(ReconcileState.UNKNOWN)

    def _complete_history(
        self,
        arguments: dict[str, Any],
        root: str,
    ) -> list[dict[str, Any]] | None:
        """Read the complete bounded cursor window or fail closed."""
        messages: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _page in range(MAX_RECONCILIATION_PAGES):
            page_arguments = dict(arguments)
            if cursor:
                page_arguments["cursor"] = cursor
            response = (
                self.client.conversations_replies(ts=root, **page_arguments)
                if root
                else self.client.conversations_history(**page_arguments)
            )
            if not response:
                return None
            page_messages = response.get("messages", [])
            if not isinstance(page_messages, list) or not all(
                isinstance(message, dict) for message in page_messages
            ):
                return None
            messages.extend(page_messages)
            metadata = response.get("response_metadata", {}) or {}
            next_cursor = str(metadata.get("next_cursor") or "").strip()
            has_more = bool(response.get("has_more"))
            if not next_cursor:
                return None if has_more else messages
            if next_cursor in seen_cursors:
                return None
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return None

    def _gable_identity(self) -> SlackIdentity | None:
        """Resolve and cache the documented bot identity without racing workers."""
        with self._identity_lock:
            if self._identity_checked:
                return self._identity
            response = self.client.auth_test()
            identity = SlackIdentity(
                user_id=str(response.get("user_id") or ""),
                bot_id=str(response.get("bot_id") or ""),
                app_id=str(response.get("app_id") or ""),
            )
            self._identity = identity if identity.known else None
            self._identity_checked = True
            return self._identity


def _oldest(created_at: str) -> str:
    """Convert the persisted UTC creation time to Slack's Unix-ts boundary."""
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return ""
    # SQLite records the outbox immediately before the API call. A small skew
    # allowance covers Slack's server clock without searching earlier history.
    return f"{max(0.0, parsed.timestamp() - _CLOCK_SKEW_SECONDS):.6f}"


def _candidate_matches(
    message: dict[str, Any],
    wanted_text: str,
    root_ts: str,
    identity: SlackIdentity,
    oldest: str,
    latest: str,
    notification_id: str,
) -> bool:
    """Apply authorship, time, text, and root/thread proof to one message."""
    timestamp = str(message.get("ts") or "").strip()
    if not timestamp or not _inside(timestamp, oldest, latest):
        return False
    if str(message.get("subtype") or "") in {"message_changed", "message_deleted"}:
        return False
    if not _authored_by_gable(message, identity):
        return False
    if canonical_visible_text(str(message.get("text") or "")) != wanted_text:
        return False
    if not _has_notification_block(message, notification_id):
        return False
    observed_client_id = str(message.get("client_msg_id") or "").strip()
    if observed_client_id and observed_client_id != notification_id:
        return False

    candidate_root = str(message.get("thread_ts") or "").strip()
    if root_ts:
        # conversations.replies includes its parent. Only an actual reply to the
        # requested root can confirm a threaded outbox item.
        return candidate_root == root_ts and timestamp != root_ts
    # Slack documents that a parent can gain thread_ts equal to its own ts after
    # replies exist. Either absent or self-identical is still a root message.
    return not candidate_root or candidate_root == timestamp


def notification_block_id(notification_id: str) -> str:
    """Return the exact Block Kit identifier written with one outbox item."""
    return f"gable_notification_{notification_id.replace('-', '_')}"[:255]


def notification_blocks(text: str, notification_id: str) -> list[dict[str, object]]:
    """Render the visible text once with a persisted reconciliation marker."""
    return [
        {
            "type": "section",
            "block_id": notification_block_id(notification_id),
            "text": {"type": "mrkdwn", "text": text},
        }
    ]


def _has_notification_block(message: dict[str, Any], notification_id: str) -> bool:
    """Require the exact persisted block id returned by Slack history."""
    blocks = message.get("blocks")
    return isinstance(blocks, list) and any(
        isinstance(block, dict)
        and str(block.get("block_id") or "") == notification_block_id(notification_id)
        for block in blocks
    )


def _authored_by_gable(message: dict[str, Any], identity: SlackIdentity) -> bool:
    """Require every observable Gable identity field to agree."""
    compared = 0
    for field, expected in (
        ("user", identity.user_id),
        ("bot_id", identity.bot_id),
        ("app_id", identity.app_id),
    ):
        observed = str(message.get(field) or "")
        if not expected or not observed:
            continue
        compared += 1
        if observed != expected:
            return False
    return compared > 0


def _inside(timestamp: str, oldest: str, latest: str) -> bool:
    """Reject malformed or out-of-window message timestamps independently of Slack."""
    try:
        return float(oldest) <= float(timestamp) <= float(latest)
    except ValueError:
        return False
