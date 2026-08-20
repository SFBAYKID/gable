"""Supply bounded Slack history and database facts to conversational turns.

The language model already accepts earlier turns and listing facts, but those
inputs are useful only when the Slack edge actually supplies them.  This module
keeps the two reads small and injectable: Slack history is fetched once per
human turn and reduced to recent prose, while listing context is reconstructed
from the existing run and submission records without adding conversational
state to the database.

This module does not decide intent, post to Slack, or mutate a flyer.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from sqlite3 import Connection
from typing import Any, Final

from gable.db import store
from gable.slackapp.brain import Decision

logger = logging.getLogger("gable.slack.context")

#: Slack recommends no more than 200 results in one conversations.replies call.
#: Listing threads are much shorter, and the second bound below keeps only the
#: immediate conversational turns that can disambiguate the new message.
MAX_FETCHED_MESSAGES: Final[int] = 200
MAX_HISTORY_PAGES: Final[int] = 5
MAX_HISTORY_TURNS: Final[int] = 12
MAX_TURN_CHARS: Final[int] = 1_200
MAX_FACT_CHARS: Final[int] = 400

History = list[tuple[str, str]]
HistoryProvider = Callable[[dict[str, Any], Any], History]
ContextProvider = Callable[[str], str]
Thinker = Callable[..., Decision]

_NAME_CACHE: dict[str, str] = {}
_MENTION: Final[re.Pattern[str]] = re.compile(r"<@[A-Z0-9]+(?:\|[^>]*)?>")
_CLIENT_FOOTER: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n)\s*\**Sent using\**(?:\s+\w+)?\s*$"
    r"|(?:^|\n)\s*via\s+Slack\s+for\s+\w+\s*$",
    re.IGNORECASE,
)


def _fact_value(value: str) -> str:
    """Normalize and bound one database value before adding it to instructions."""
    return " ".join(value.split())[:MAX_FACT_CHARS]


def clean_mention_text(text: str) -> str:
    """Strip Slack mention markup and client footers from conversational text.

    Args:
        text: Raw Slack message text.

    Returns:
        Plain words suitable for the language-model input.

    Raises:
        Nothing.
    """
    without_mentions = _MENTION.sub(" ", text or "")
    without_footer = _CLIENT_FOOTER.sub(" ", without_mentions)
    return " ".join(without_footer.split())


def first_name_of(client: Any, user_id: str) -> str:  # noqa: ANN401 - Slack WebClient
    """Return the Slack speaker's first name when Slack can supply it.

    Args:
        client: Slack Web API client injected by Bolt.
        user_id: Stable Slack user identifier.

    Returns:
        First name, or an empty string when lookup fails.

    Raises:
        Nothing. A missing greeting name must not suppress the real answer.
    """
    if not user_id:
        return ""
    cached = _NAME_CACHE.get(user_id)
    if cached is not None:
        return cached
    name = ""
    try:
        profile = client.users_info(user=user_id).get("user", {}) or {}
        details = profile.get("profile", {}) or {}
        full = (
            details.get("first_name") or details.get("real_name") or profile.get("real_name") or ""
        )
        name = str(full).split(" ")[0]
    except Exception:
        logger.debug("could not resolve the speaker's name")
    _NAME_CACHE[user_id] = name
    return name


def thread_history(
    event: dict[str, Any],
    client: Any,  # noqa: ANN401 - Slack WebClient is untyped upstream
    *,
    allowed_user_ids: frozenset[str] = frozenset(),
    bot_user_id: str = "",
    bot_id: str = "",
) -> History:
    """Read recent messages that precede one Slack thread event.

    Args:
        event: Current Slack message or app-mention event.
        client: Slack Web API client injected by Bolt.
        allowed_user_ids: Stable human IDs whose prior words may affect Gable.
        bot_user_id: Gable's own Slack user ID, from Bolt context.
        bot_id: Gable's own Slack bot ID, from Bolt context.

    Returns:
        At most twelve prior turns, oldest first. Bot-authored turns are marked
        as Gable so ``brain.think`` gives them the assistant role; human turns
        receive the user role.

    Raises:
        Nothing. Slack history trouble removes context but never drops a reply.
    """
    channel = str(event.get("channel") or "")
    thread_ts = str(event.get("thread_ts") or "")
    current_ts = str(event.get("ts") or "")
    if not channel or not thread_ts:
        return []
    raw_messages: list[Any] = []
    cursor = ""
    try:
        for _page in range(MAX_HISTORY_PAGES):
            # Verified vendor contract: conversations.replies accepts channel,
            # root ts, latest, inclusive, limit and cursor, and returns each
            # page oldest first.
            # https://docs.slack.dev/reference/methods/conversations.replies/
            arguments: dict[str, Any] = {
                "channel": channel,
                "ts": thread_ts,
                "latest": current_ts or None,
                "inclusive": False,
                "limit": MAX_FETCHED_MESSAGES,
            }
            if cursor:
                arguments["cursor"] = cursor
            response = client.conversations_replies(**arguments)
            page_messages = response.get("messages", []) if response else []
            if isinstance(page_messages, list):
                raw_messages.extend(page_messages)
            metadata = response.get("response_metadata", {}) if response else {}
            cursor = str(metadata.get("next_cursor") or "")
            if not cursor:
                break
    except Exception:
        # Slack SDK exceptions can contain response details. Do not copy them
        # into logs; a context miss is enough to diagnose at this boundary.
        logger.warning("could not read prior messages from the Slack thread")
        return []

    # More than one thousand prior messages is not a useful flyer conversation,
    # and feeding an old prefix as though it were recent is worse than asking
    # again. Fail closed if the explicit page ceiling did not reach the event.
    if cursor:
        logger.warning("Slack thread history exceeded the bounded context window")
        return []

    turns: History = []
    for raw in raw_messages:
        if not isinstance(raw, dict):
            continue
        if current_ts and str(raw.get("ts") or "") == current_ts:
            continue
        subtype = str(raw.get("subtype") or "")
        if subtype in {"message_deleted", "message_changed"}:
            continue
        text = clean_mention_text(str(raw.get("text") or ""))
        if not text:
            continue
        raw_user = str(raw.get("user") or "")
        raw_bot = str(raw.get("bot_id") or "")
        is_bot_message = bool(raw_bot or raw.get("app_id") or subtype == "bot_message")
        identity_filtering = bool(allowed_user_ids or bot_user_id or bot_id)
        is_gable = (
            (bool(bot_user_id) and raw_user == bot_user_id)
            or (bool(bot_id) and raw_bot == bot_id)
            or (not identity_filtering and is_bot_message)
        )
        if is_bot_message and not is_gable:
            continue
        if not is_gable and allowed_user_ids and raw_user not in allowed_user_ids:
            continue
        speaker = "Gable" if is_gable else "user"
        turns.append((speaker, text[:MAX_TURN_CHARS]))
    return turns[-MAX_HISTORY_TURNS:]


def listing_context(connection: Connection, thread_ts: str) -> str:
    """Describe the listing or source template owned by one Slack thread.

    Args:
        connection: Open Gable database connection.
        thread_ts: Root timestamp identifying the owned Slack conversation.

    Returns:
        Short factual context for the conversation model, or an empty string
        when the thread is not attached to persisted Gable work.

    Raises:
        sqlite3.Error: When the existing database records cannot be read.
    """
    run = store.run_for_thread(connection, thread_ts)
    if run is not None:
        facts = [f"Run status: {run.status}."]
        stored = store.load_submission(connection, run.response_row_id)
        if stored is not None:
            intake = stored.intake
            if intake.request_type:
                facts.append(f"Request type: {_fact_value(intake.request_type)}.")
            if intake.address:
                facts.append(f"Property address: {_fact_value(intake.address)}.")
            if intake.agent_name:
                facts.append(f"Submitting agent: {_fact_value(intake.agent_name)}.")
            if intake.agent_email:
                facts.append(f"Submitted agent email: {_fact_value(intake.agent_email)}.")
        if run.template_label:
            facts.append(f"Selected template: {_fact_value(run.template_label)}.")
        facts.append(
            "A flyer exists in this thread."
            if run.output_file_id
            else "No flyer has been built in this thread yet."
        )
        # `photo_url` survives a refusal as audit evidence for the image that
        # was rejected, so it is not the question "does this run have a usable
        # photograph". Saying it does, while Gable is asking for another one,
        # invited the model to argue with its own outstanding request.
        rejected = store.photo_was_rejected(connection, run.run_id)
        if run.photo_url and not rejected:
            facts.append("A human-supplied hero photo is attached to this run.")
        elif run.photo_url:
            facts.append("This run holds a hero photo that a check refused; another is needed.")
        else:
            facts.append("This run has no hero photo yet.")
        # The status names the work only a person can do; it does not name
        # everything the run is waiting for. A run owed a widened design AND a
        # photograph parks in needs_template, and the reply shortcuts that read
        # only the status could not tell that a photo was still wanted.
        if run.awaiting_photo:
            facts.append("This run has asked for the property photo and is waiting for it.")
        if run.is_paused and run.failure_reason:
            facts.append(f"The run is waiting because: {_fact_value(run.failure_reason)}")
        return "\n".join(facts)

    template = store.template_for_thread(connection, thread_ts)
    if template is None:
        return ""
    facts = [
        f"This thread is about the source template named {_fact_value(template.name)}.",
        f"Template review status: {template.status}.",
    ]
    if template.summary:
        facts.append(f"Latest template review: {_fact_value(template.summary)}")
    return "\n".join(facts)


def decide_with_context(
    thinker: Thinker,
    message: str,
    speaker: str,
    event: dict[str, Any],
    client: Any,  # noqa: ANN401 - Slack WebClient
    history_provider: HistoryProvider | None,
    context_provider: ContextProvider | None,
) -> Decision:
    """Call a conversation thinker with all available thread context.

    Args:
        thinker: Intent model or an injectable test double.
        message: Current human message.
        speaker: Resolved first name, if known.
        event: Current Slack event.
        client: Slack Web API client used by the history provider.
        history_provider: Optional bounded prior-turn reader.
        context_provider: Optional database listing-fact reader.

    Returns:
        The thinker's decision.

    Raises:
        Any exception raised by the thinker. The enclosing Slack handler owns
        the plain-language failure response.
    """
    history: History = []
    if history_provider is not None:
        try:
            history = history_provider(event, client)
        except Exception:
            logger.exception("the injected Slack history provider failed")
    thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
    context = ""
    if context_provider is not None:
        try:
            context = context_provider(thread_ts)
        except Exception:
            logger.exception("the injected listing-context provider failed")
    if history or context:
        return thinker(message, history=history, context=context, speaker=speaker)
    # Preserve the narrow callable used by isolated connection and unit tests;
    # production receives the richer signature as soon as either provider has
    # something authoritative to add.
    return thinker(message, speaker=speaker)
