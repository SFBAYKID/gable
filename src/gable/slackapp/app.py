"""The Slack process: listens on Socket Mode, answers, and never leaks.

Socket Mode opens an outbound WebSocket, so there are no inbound ports, no TLS
certificate and no domain (ARCHITECTURE.md §2.2). The cost is that the Sheet has
to be polled rather than pushed, which `pipeline/schedule.py` handles.

Three rules govern everything below, and they are enforced here rather than
trusted to the caller:

1. **Every outgoing message passes `style.violations()` first.** A handler that
   builds a bad message gets a safe fallback instead; Carmen never sees the
   breach.
2. **A handler never raises into Slack.** An unhandled exception in an event
   handler is a message that silently never arrives, which reads to Carmen as
   Gable ignoring her. Everything is caught and turned into a sentence.
3. **A thread timestamp is not ownership.** Direct mentions always reach Gable;
   ordinary replies and photos reach it only under a Gable-owned root.
4. **Gable posts to one channel.** The mention handler replies where it was
   spoken to; nothing else broadcasts.

Production construction and Socket Mode lifecycle live in
``gable.slackapp.runtime``; importing this module performs no I/O.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final

from gable.slackapp.brain import Decision, think
from gable.slackapp.client import build_web_client
from gable.slackapp.context import (
    ContextProvider,
    HistoryProvider,
    clean_mention_text,
    decide_with_context,
    first_name_of,
    thread_history,
)
from gable.slackapp.photos import process_file_share
from gable.slackapp.routing import (
    EventReplayGuard,
    MessageRoute,
    ThreadOwnership,
    has_shared_files,
    speaks_to_gable_at_top_level,
)
from gable.slackapp.status import Working
from gable.slackapp.style import is_clean, strip_to_plain

logger = logging.getLogger("gable.slack")

#: What Gable says when it genuinely cannot form a reply. Deliberately not an
#: apology loop: it says what it can still do.
FALLBACK: Final[str] = (
    "I could not finish answering that Slack request because the reply step failed. "
    "I did not make or confirm a flyer change. Ask me again."
)

ProgressReporter = Callable[[str], None]
FileShareHandler = Callable[[dict[str, Any], Any, ProgressReporter], str]
ActionHandler = Callable[[Decision, str, ProgressReporter, str], str]
Thinker = Callable[..., Decision]

ACTION_STAGES: Final[dict[str, str]] = {
    "set_font_size": "is updating the flyer text...",
    "set_colour": "is recolouring the flyer...",
    "resize_photo": "is resizing the flyer photo...",
    "replace_photo": "is preparing the photo replacement...",
    "move_element": "is moving an element on the flyer...",
    "correct_field": "is correcting the flyer...",
    "rebuild_flyer": "is rebuilding the flyer...",
    "report_status": "is checking the flyer status...",
}


def _scoped_history_provider(
    provider: HistoryProvider | None,
    allowed_user_ids: frozenset[str],
    bot_user_id: str,
    bot_id: str,
) -> HistoryProvider | None:
    """Bind the default Slack reader to Gable and its two allowed people."""
    if provider is not thread_history:
        return provider

    def read(event: dict[str, Any], client: Any) -> list[tuple[str, str]]:  # noqa: ANN401
        """Read prior turns without letting another app influence an action."""
        return thread_history(
            event,
            client,
            allowed_user_ids=allowed_user_ids,
            bot_user_id=bot_user_id,
            bot_id=bot_id,
        )

    return read


def stage_for_decision(decision: Decision) -> str:
    """Describe the real work an action decision is about to perform.

    Args:
        decision: Model-selected conversational outcome.

    Returns:
        Present-tense native-status copy. Conversation and clarification retain
        the generic answer-preparation stage.

    Raises:
        Nothing.
    """
    return ACTION_STAGES.get(decision.tool, "is preparing the answer...")


def speaker_allowed(user_id: str, allowed_user_ids: frozenset[str]) -> bool:
    """Return whether a Slack event came from Carmen or Chase.

    An empty set exists only for isolated unit construction. Production config
    requires explicit stable user IDs; display names are not an access check.
    """
    return not allowed_user_ids or user_id in allowed_user_ids


def safe_reply(text: str) -> str:
    """The last gate before anything reaches Slack.

    Args:
        text: The candidate message.

    Returns:
        `text` if it obeys the house style, a scrubbed version if that is
        enough, otherwise `FALLBACK`.

    Raises:
        Nothing.
    """
    if is_clean(text):
        return text
    scrubbed = strip_to_plain(text)
    if is_clean(scrubbed):
        logger.warning("scrubbed a non-compliant reply before posting")
        return scrubbed
    logger.error("replaced a reply that could not be made compliant")
    return FALLBACK


def describe_action(decision: Decision) -> str:
    """Return no progress claim until an action executor reports success.

    Args:
        decision: What the brain concluded.

    Returns:
        An empty string. The old implementation announced every selected tool
        even though no handler executed it, violating Gable's core honesty
        rule. A real executor owns any future completion message.

    Raises:
        Nothing.
    """
    del decision
    return ""


def reply_for_decision(
    decision: Decision,
    action_handler: ActionHandler | None = None,
    thread_ts: str = "",
    progress: ProgressReporter = lambda _stage: None,
    action_id: str = "",
) -> str:
    """Choose an honest reply while action execution is being connected.

    Args:
        decision: What the conversational model selected.
        action_handler: Executes a selected tool against the thread's flyer.
        thread_ts: Thread that identifies the database run.
        progress: Updates the same native waiting state while a long action
            moves through its real stages.
        action_id: Stable Slack event identity used to suppress action replay.

    Returns:
        The model's reply for conversation and clarification. For an action no
        handler executes yet, a precise refusal that cannot be mistaken for a
        completed edit.

    Raises:
        Nothing.
    """
    if not decision.wants_action or decision.tool == "ask_clarifying":
        return decision.reply
    if action_handler is not None:
        return action_handler(decision, thread_ts, progress, action_id)
    return "I understood the change, but I could not apply it. I have not changed the flyer."


def process_mention(
    event: dict[str, Any],
    say: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    thinker: Thinker,
    file_share_handler: FileShareHandler | None = None,
    action_handler: ActionHandler | None = None,
    history_provider: HistoryProvider | None = thread_history,
    context_provider: ContextProvider | None = None,
) -> None:
    """Route a direct mention carrying a file to the photo workflow.

    Slack's app-mention event shape permits a ``files`` array. Treating every
    mention as prose sends the filename to the language model and leaves the
    listing paused even though Carmen supplied exactly what Gable requested.
    An explicit mention still bypasses root ownership, but the photo handoff
    independently requires a database run owned by that exact thread.

    Args:
        event: Slack app-mention event.
        say: Bolt's thread-aware posting helper.
        client: Slack Web API client.
        thinker: Conversation decision function.
        file_share_handler: Production photo workflow, when connected.
        action_handler: Executes model-selected flyer actions.
        history_provider: Reads prior messages in the same thread.
        context_provider: Resolves persisted listing facts for that thread.

    Raises:
        Nothing. The delegated handlers own their plain-language failures.
    """
    # A declined upload leaves the mention's own words to be answered — the
    # message-path fix taught `process_file_share` to say so, and this caller
    # was still discarding that signal. A mention carrying the asked-for
    # address plus a photo the run could not take was answered with nothing at
    # all, which is worse than the pre-fix "not waiting for a photo".
    if has_shared_files(event) and process_file_share(event, say, client, file_share_handler):
        return
    answer_mention(
        event,
        say,
        client,
        thinker,
        action_handler,
        history_provider,
        context_provider,
    )


def answer_mention(
    event: dict[str, Any],
    say: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    thinker: Thinker,
    action_handler: ActionHandler | None = None,
    history_provider: HistoryProvider | None = thread_history,
    context_provider: ContextProvider | None = None,
) -> None:
    """Compose and post one reply to a mention, with the indicator over the wait.

    Lifted out of the Bolt closure so it can be tested without a workspace: this
    is the path Chase has rejected twice and it had no test at all.

    Args:
        event: Slack's `app_mention` event.
        say: Bolt's thread-aware posting helper.
        client: Slack Web API client, used for the name lookup and the indicator.
        thinker: Turns what was asked into a decision.
        action_handler: Executes a model-selected edit, when one is wired.
        history_provider: Reads bounded prior messages for a threaded mention.
        context_provider: Resolves the thread to persisted listing facts.

    Raises:
        Nothing. An exception here is a message Carmen never receives, which
        reads as Gable ignoring her, so it becomes a sentence instead.
    """
    try:
        asked = clean_mention_text(event.get("text", ""))
        thread = event.get("thread_ts") or event.get("ts")
        logger.info("mention received: %s", asked[:120])
        # The indicator goes up before the first network call. The name lookup
        # is itself a round trip, so one raised after it has already missed part
        # of the wait it exists to cover.
        with Working(client, str(event.get("channel") or ""), str(thread or "")) as waiting:
            try:
                speaker = first_name_of(client, str(event.get("user") or ""))
                if not asked:
                    greeting = (
                        f"Hi {speaker}. What would you like me to do?"
                        if speaker
                        else "I'm here. What would you like me to do?"
                    )
                    say(text=safe_reply(greeting), thread_ts=thread)
                    return
                decision = decide_with_context(
                    thinker,
                    asked,
                    speaker,
                    event,
                    client,
                    history_provider,
                    context_provider,
                )
                logger.info("replying (tool=%s)", decision.tool or "none")
                waiting.stage(stage_for_decision(decision))
                answer = safe_reply(
                    reply_for_decision(
                        decision,
                        action_handler,
                        str(thread or ""),
                        waiting.stage,
                        str(event.get("client_msg_id") or event.get("ts") or ""),
                    )
                )
                # Said inside the block on purpose: the native state remains up
                # until the answer is in the thread, then Slack clears it.
                if answer:
                    say(text=answer, thread_ts=thread)
            except Exception:
                logger.exception("mention response failed")
                say(text=FALLBACK, thread_ts=thread)
                return
        follow_up = describe_action(decision)
        if follow_up:
            say(text=safe_reply(follow_up), thread_ts=thread)
    except Exception:
        logger.exception("mention handler failed")
        try:
            say(text=FALLBACK, thread_ts=event.get("thread_ts") or event.get("ts"))
        except Exception:
            logger.exception("could not deliver the fallback either")


def answer_thread_reply(
    event: dict[str, Any],
    say: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    thinker: Thinker,
    action_handler: ActionHandler | None = None,
    history_provider: HistoryProvider | None = thread_history,
    context_provider: ContextProvider | None = None,
) -> None:
    """Answer one follow-up in an existing Gable thread with waiting feedback.

    Args:
        event: Slack's ordinary threaded message event.
        say: Bolt's thread-aware posting helper.
        client: Slack Web API client for user lookup and native status.
        thinker: Turns the follow-up into a decision.
        action_handler: Executes a selected flyer edit when one is wired.
        history_provider: Reads bounded prior messages from the owned thread.
        context_provider: Resolves the thread to persisted listing facts.

    Raises:
        Nothing. Failures become the same safe sentence as initial mentions.
    """
    thread = str(event.get("thread_ts") or "")
    if not thread:
        return
    try:
        asked = clean_mention_text(event.get("text", ""))
        logger.info("thread reply received: %s", asked[:120])
        with Working(client, str(event.get("channel") or ""), thread) as waiting:
            try:
                speaker = first_name_of(client, str(event.get("user") or ""))
                if not asked:
                    answer = "What would you like me to do next?"
                else:
                    decision = decide_with_context(
                        thinker,
                        asked,
                        speaker,
                        event,
                        client,
                        history_provider,
                        context_provider,
                    )
                    logger.info("replying to thread (tool=%s)", decision.tool or "none")
                    waiting.stage(stage_for_decision(decision))
                    answer = reply_for_decision(
                        decision,
                        action_handler,
                        thread,
                        waiting.stage,
                        str(event.get("client_msg_id") or event.get("ts") or ""),
                    )
                if answer:
                    say(text=safe_reply(answer), thread_ts=thread)
            except Exception:
                logger.exception("thread response failed")
                say(text=FALLBACK, thread_ts=thread)
    except Exception:
        logger.exception("thread reply handler failed")
        try:
            say(text=FALLBACK, thread_ts=thread)
        except Exception:
            logger.exception("could not deliver the thread fallback either")


def build_app(
    bot_token: str,
    file_share_handler: FileShareHandler | None = None,
    action_handler: ActionHandler | None = None,
    allowed_channel: str = "",
    allowed_user_ids: frozenset[str] = frozenset(),
    thinker: Thinker = think,
    history_provider: HistoryProvider | None = thread_history,
    context_provider: ContextProvider | None = None,
) -> Any:  # noqa: ANN401 - slack_bolt.App, imported lazily
    """Construct the Bolt app with its handlers registered.

    Args:
        bot_token: Validated single-workspace bot credential.
        file_share_handler: Production photo workflow. Optional so import and
            isolated conversation tests need no Google or database clients.
        action_handler: Executes model-selected edits against a thread's flyer.
        allowed_channel: The only channel Gable may answer. Blank preserves the
            isolated app bootstrap used by connection checks.
        allowed_user_ids: The only two people Gable may answer in production.
        thinker: Conversation decision function. Production supplies a
            budget-guarded wrapper; isolated checks use the pure default.
        history_provider: Reads bounded prior turns from the current thread.
        context_provider: Resolves an owned thread to listing or template facts.

    Returns:
        A configured `slack_bolt.App`.

    Raises:
        RuntimeError: if the bot token is absent. Failing at construction is
            right: a process that starts without a token looks healthy in
            systemd and answers nobody.
    """
    from slack_bolt import App

    if not bot_token:
        msg = "SLACK_BOT_TOKEN is not set, so Gable would start and answer nobody"
        raise RuntimeError(msg)

    app = App(client=build_web_client(bot_token), signing_secret="", logger=logger)
    thread_ownership = ThreadOwnership(allowed_user_ids=allowed_user_ids)
    replay_guard = EventReplayGuard()

    @app.event("app_mention")
    def handle_mention(
        event: dict[str, Any],
        say: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
        client: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
        context: Any = None,  # noqa: ANN401 - Bolt injection, untyped upstream
    ) -> None:
        """Answer a direct mention, in the channel Gable is allowed to speak in."""
        if allowed_channel and event.get("channel") != allowed_channel:
            return
        if not speaker_allowed(str(event.get("user") or ""), allowed_user_ids):
            return
        if not replay_guard.first_delivery(event, route="human_message"):
            return
        # Only a top-level mention creates an owned Gable conversation. A direct
        # mention inside another app's thread remains one-shot by contract.
        if not event.get("thread_ts"):
            thread_ownership.remember_owned(
                str(event.get("channel") or ""),
                str(event.get("ts") or ""),
            )
        bolt_context = context or {}
        scoped_history = _scoped_history_provider(
            history_provider,
            allowed_user_ids,
            str(bolt_context.get("bot_user_id") or ""),
            str(bolt_context.get("bot_id") or ""),
        )
        process_mention(
            event,
            say,
            client,
            thinker,
            file_share_handler,
            action_handler,
            scoped_history,
            context_provider,
        )

    @app.event("message")
    def handle_message(
        event: dict[str, Any],
        say: Any,  # noqa: ANN401
        client: Any,  # noqa: ANN401
        context: Any,  # noqa: ANN401 - Bolt context, untyped upstream
    ) -> None:
        """Answer only inside a thread Gable owns.

        Direct mentions have their own listener. This ordinary-message path is
        silent in threads rooted by Monarch or any other app.
        """
        try:
            if allowed_channel and event.get("channel") != allowed_channel:
                return
            if not speaker_allowed(str(event.get("user") or ""), allowed_user_ids):
                return
            # Being named in the channel is being spoken to. Carmen wrote
            # "Thanks Gable!" at top level and got nothing, because an ordinary
            # message outside a Gable thread was ignored by design — which reads
            # as being snubbed rather than as a routing rule. A top-level message
            # that names Gable takes the same path as a mention, and becomes an
            # owned thread so the follow-ups work too. Messages that do not name
            # Gable are still ignored: this channel carries Carmen and Chase
            # talking to each other, and Gable does not interrupt that.
            if speaks_to_gable_at_top_level(event, str(context.get("bot_user_id") or "")):
                if not replay_guard.first_delivery(event, route="human_message"):
                    return
                thread_ownership.remember_owned(
                    str(event.get("channel") or ""),
                    str(event.get("ts") or ""),
                )
                process_mention(
                    event,
                    say,
                    client,
                    thinker,
                    file_share_handler,
                    action_handler,
                    _scoped_history_provider(
                        history_provider,
                        allowed_user_ids,
                        str(context.get("bot_user_id") or ""),
                        str(context.get("bot_id") or ""),
                    ),
                    context_provider,
                )
                return
            route = thread_ownership.route(
                event,
                client,
                bot_user_id=str(context.get("bot_user_id") or ""),
                bot_id=str(context.get("bot_id") or ""),
            )
            if route not in {MessageRoute.FILE_SHARE, MessageRoute.THREAD_REPLY}:
                return
            if not replay_guard.first_delivery(event, route="human_message"):
                return
            # A message carrying a file used to end here whatever it said. One
            # reply gave the address Gable had just asked for and attached the
            # photo; the run was not waiting for a photo, so the upload was
            # declined and the address went with it. An upload the run cannot
            # use does not make the words beside it meaningless, so a declined
            # photo falls through to the reply path rather than ending the
            # message.
            if route is MessageRoute.FILE_SHARE and process_file_share(
                event, say, client, file_share_handler
            ):
                return
            scoped_history = _scoped_history_provider(
                history_provider,
                allowed_user_ids,
                str(context.get("bot_user_id") or ""),
                str(context.get("bot_id") or ""),
            )
            answer_thread_reply(
                event,
                say,
                client,
                thinker,
                action_handler,
                scoped_history,
                context_provider,
            )
        except Exception:
            logger.exception("message handler failed")

    return app
