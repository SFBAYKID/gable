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
import re
from collections.abc import Callable
from typing import Any, Final

from gable.slackapp.brain import Decision, think
from gable.slackapp.routing import MessageRoute, ThreadOwnership
from gable.slackapp.status import Working
from gable.slackapp.style import is_clean, strip_to_plain

logger = logging.getLogger("gable.slack")

#: Slack user id to first name. Names do not change mid-conversation, and the
#: lookup is not worth repeating on every message.
_NAME_CACHE: dict[str, str] = {}

#: `<@U123ABC>` — Slack's own mention markup, stripped before the model sees it
#: so "hello" arrives as "hello" rather than as an id.
_MENTION: Final[re.Pattern[str]] = re.compile(r"<@[A-Z0-9]+(?:\|[^>]*)?>")

#: Footers some Slack clients append to a message. They are not part of what the
#: person typed, and feeding them to the model makes "hello" arrive as "hello
#: Sent using Claude" — which is noise at best and a misread instruction at
#: worst.
_CLIENT_FOOTER: Final[re.Pattern[str]] = re.compile(
    r"\**Sent using\**\s*\w+\s*$|\bvia\s+Slack\s+for\s+\w+\s*$", re.IGNORECASE
)

#: What Gable says when it genuinely cannot form a reply. Deliberately not an
#: apology loop: it says what it can still do.
FALLBACK: Final[str] = (
    "I could not finish answering that Slack request because the reply step failed. "
    "I did not make or confirm a flyer change. Ask me again."
)

ProgressReporter = Callable[[str], None]
FileShareHandler = Callable[[dict[str, Any], Any, ProgressReporter], str]
ActionHandler = Callable[[Decision, str, ProgressReporter], str]
Thinker = Callable[..., Decision]

ACTION_STAGES: Final[dict[str, str]] = {
    "set_font_size": "is updating the flyer text...",
    "set_colour": "is recolouring the flyer...",
    "resize_photo": "is resizing the flyer photo...",
    "move_element": "is moving an element on the flyer...",
    "correct_field": "is correcting the flyer...",
    "rebuild_flyer": "is rebuilding the flyer...",
    "report_status": "is checking the flyer status...",
}


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


def clean_mention_text(text: str) -> str:
    """Strip Slack mention markup and tidy whitespace.

    Args:
        text: The raw `event["text"]`.

    Returns:
        What the person actually typed.

    Raises:
        Nothing.
    """
    without_mentions = _MENTION.sub(" ", text or "")
    without_footer = _CLIENT_FOOTER.sub(" ", without_mentions)
    return " ".join(without_footer.split())


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
) -> str:
    """Choose an honest reply while action execution is being connected.

    Args:
        decision: What the conversational model selected.
        action_handler: Executes a selected tool against the thread's flyer.
        thread_ts: Thread that identifies the database run.
        progress: Updates the same native waiting state while a long action
            moves through its real stages.

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
        return action_handler(decision, thread_ts, progress)
    return "I understood the change, but I could not apply it. I have not changed the flyer."


def process_file_share(
    event: dict[str, Any],
    say: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    handler: FileShareHandler | None,
) -> None:
    """Fit the shared photo, showing the thinking indicator while it runs.

    Fitting a photo and rendering the flyer takes about thirty seconds — the
    longest silence Carmen ever sees — so this is the path the indicator matters
    most on.

    A failure must say so. An earlier version posted "Fitting it to the flyer
    now" and edited that message into the outcome; when the fit raised, the
    sentence stayed in the thread claiming work that had already died.

    Args:
        event: Slack's file-share message event.
        say: Bolt's thread-aware posting helper.
        client: Slack Web API client, used for the indicator.
        handler: The real photo workflow, or None in isolated startup checks.

    Raises:
        Nothing. Every outcome, including failure, is said out loud.
    """
    thread = event.get("thread_ts") or event.get("ts")
    if handler is None:
        say(
            text=safe_reply(
                "I received the photo, but photo processing is not available right now."
            ),
            thread_ts=thread,
        )
        return
    with Working(
        client,
        str(event.get("channel") or ""),
        str(thread or ""),
        "is building the flyer...",
    ) as waiting:
        try:
            spoken = handler(event, client, waiting.stage)
            # An empty outcome means the run already said everything in the
            # thread. Saying nothing is the correct message then.
            if not spoken.strip():
                return
            outcome = safe_reply(spoken)
        except Exception:
            logger.exception("the photo workflow failed")
            outcome = (
                "I could not fit that photo to the flyer. The flyer is unchanged — "
                "send it again, or tell me which listing it belongs to."
            )
        say(text=outcome, thread_ts=thread)


def first_name_of(client: Any, user_id: str) -> str:  # noqa: ANN401 - Slack WebClient
    """The speaker's first name, or empty when it cannot be looked up.

    Greeting the room when one person asked the question reads as not listening,
    so the name is worth a round trip. A failure here is not worth failing the
    reply over — an unnamed greeting is merely worse, not wrong.

    Args:
        client: A Slack WebClient.
        user_id: The Slack id of whoever spoke.

    Returns:
        Their first name, or an empty string.

    Raises:
        Nothing.
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


def answer_mention(
    event: dict[str, Any],
    say: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    thinker: Thinker,
    action_handler: ActionHandler | None = None,
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
                decision = thinker(asked, speaker=speaker)
                logger.info("replying (tool=%s)", decision.tool or "none")
                waiting.stage(stage_for_decision(decision))
                answer = safe_reply(
                    reply_for_decision(
                        decision,
                        action_handler,
                        str(thread or ""),
                        waiting.stage,
                    )
                )
                # Said inside the block on purpose: the native state remains up
                # until the answer is in the thread, then Slack clears it.
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
) -> None:
    """Answer one follow-up in an existing Gable thread with waiting feedback.

    Args:
        event: Slack's ordinary threaded message event.
        say: Bolt's thread-aware posting helper.
        client: Slack Web API client for user lookup and native status.
        thinker: Turns the follow-up into a decision.
        action_handler: Executes a selected flyer edit when one is wired.

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
                    decision = thinker(asked, speaker=speaker)
                    logger.info("replying to thread (tool=%s)", decision.tool or "none")
                    waiting.stage(stage_for_decision(decision))
                    answer = reply_for_decision(
                        decision,
                        action_handler,
                        thread,
                        waiting.stage,
                    )
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

    app = App(token=bot_token, signing_secret="", logger=logger)
    thread_ownership = ThreadOwnership()

    @app.event("app_mention")
    def handle_mention(event: dict[str, Any], say: Any, client: Any) -> None:  # noqa: ANN401
        """Answer a direct mention, in the channel Gable is allowed to speak in."""
        if allowed_channel and event.get("channel") != allowed_channel:
            return
        if not speaker_allowed(str(event.get("user") or ""), allowed_user_ids):
            return
        answer_mention(event, say, client, thinker, action_handler)

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
            route = thread_ownership.route(
                event,
                client,
                bot_user_id=str(context.get("bot_user_id") or ""),
                bot_id=str(context.get("bot_id") or ""),
            )
            if route is MessageRoute.FILE_SHARE:
                process_file_share(event, say, client, file_share_handler)
                return
            if route is MessageRoute.THREAD_REPLY:
                answer_thread_reply(event, say, client, thinker, action_handler)
        except Exception:
            logger.exception("message handler failed")

    return app
