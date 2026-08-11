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
3. **Gable posts to one channel.** The mention handler replies where it was
   spoken to; nothing else broadcasts.

Run it with `python -m gable.slackapp.app`, or under the systemd unit in
`deploy/`.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Callable
from typing import Any, Final

from gable.slackapp.brain import Decision, think
from gable.slackapp.style import is_clean, strip_to_plain

logger = logging.getLogger("gable.slack")

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
    "Something went wrong at my end just now. Ask me again, or tell me which "
    "listing you mean and I'll pick it up from there."
)

FileShareHandler = Callable[[dict[str, Any], Any], str]
ActionHandler = Callable[[Decision, str], str]
Thinker = Callable[[str], Decision]


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
) -> str:
    """Choose an honest reply while action execution is being connected.

    Args:
        decision: What the conversational model selected.
        action_handler: Executes a selected tool against the thread's flyer.
        thread_ts: Thread that identifies the database run.

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
        return action_handler(decision, thread_ts)
    return "I understood the change, but I could not apply it. I have not changed the flyer."


def process_file_share(
    event: dict[str, Any],
    say: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    handler: FileShareHandler | None,
) -> None:
    """Show one progress message and replace it with the photo outcome.

    Args:
        event: Slack's file-share message event.
        say: Bolt's thread-aware posting helper.
        client: Slack Web API client, used to edit the progress message.
        handler: The real photo workflow, or None in isolated startup checks.

    Raises:
        Nothing. The surrounding event handler owns its final exception gate.
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
    progress = say(
        text=safe_reply("I have the photo. Fitting it to the flyer now."),
        thread_ts=thread,
    )
    outcome = safe_reply(handler(event, client))
    progress_ts = str(progress.get("ts") or "") if hasattr(progress, "get") else ""
    if progress_ts:
        client.chat_update(
            channel=event.get("channel"),
            ts=progress_ts,
            text=outcome,
        )
    else:
        say(text=outcome, thread_ts=thread)


def build_app(
    file_share_handler: FileShareHandler | None = None,
    action_handler: ActionHandler | None = None,
    allowed_channel: str = "",
    thinker: Thinker = think,
) -> Any:  # noqa: ANN401 - slack_bolt.App, imported lazily
    """Construct the Bolt app with its handlers registered.

    Args:
        file_share_handler: Production photo workflow. Optional so import and
            isolated conversation tests need no Google or database clients.
        action_handler: Executes model-selected edits against a thread's flyer.
        allowed_channel: The only channel Gable may answer. Blank preserves the
            isolated app bootstrap used by connection checks.
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

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        msg = "SLACK_BOT_TOKEN is not set, so Gable would start and answer nobody"
        raise RuntimeError(msg)

    # Bolt auto-enables an OAuth installation store when it sees SLACK_CLIENT_ID
    # and SLACK_CLIENT_SECRET, and then *ignores the bot token entirely* — it
    # says so in a warning that is easy to miss in a healthy-looking startup log.
    # Gable is a single-workspace app installed once; those two variables are
    # kept only so a future distribution does not need a scavenger hunt. Hiding
    # them here keeps Bolt on the token we actually authenticate with.
    for oauth_only in ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"):
        os.environ.pop(oauth_only, None)

    app = App(token=token, logger=logger)

    @app.event("app_mention")
    def handle_mention(event: dict[str, Any], say: Any) -> None:  # noqa: ANN401
        """Answer a direct mention.

        Replies in the thread when spoken to in one, so a conversation about a
        listing stays with that listing.
        """
        try:
            if allowed_channel and event.get("channel") != allowed_channel:
                return
            asked = clean_mention_text(event.get("text", ""))
            thread = event.get("thread_ts") or event.get("ts")
            logger.info("mention received: %s", asked[:120])
            if not asked:
                say(text=safe_reply("I'm here. What would you like me to do?"), thread_ts=thread)
                return
            decision = thinker(asked)
            logger.info("replying (tool=%s)", decision.tool or "none")
            say(
                text=safe_reply(reply_for_decision(decision, action_handler, str(thread or ""))),
                thread_ts=thread,
            )
            follow_up = describe_action(decision)
            if follow_up:
                say(text=safe_reply(follow_up), thread_ts=thread)
        except Exception:
            # An exception here is a message Carmen never receives, which reads
            # as Gable ignoring her. Say something instead.
            logger.exception("mention handler failed")
            try:
                say(text=FALLBACK, thread_ts=event.get("thread_ts") or event.get("ts"))
            except Exception:
                logger.exception("could not deliver the fallback either")

    @app.event("message")
    def handle_message(
        event: dict[str, Any],
        say: Any,  # noqa: ANN401
        client: Any,  # noqa: ANN401
    ) -> None:
        """Answer a reply in a thread Gable is already part of.

        Ignores everything else, including its own messages, so the channel does
        not become a place where Gable talks to itself.
        """
        try:
            if allowed_channel and event.get("channel") != allowed_channel:
                return
            if event.get("bot_id"):
                return
            if event.get("subtype") == "file_share":
                process_file_share(event, say, client, file_share_handler)
                return
            if event.get("subtype"):
                return
            if not event.get("thread_ts"):
                return  # a top-level message that did not mention us
            decision = thinker(clean_mention_text(event.get("text", "")))
            say(
                text=safe_reply(
                    reply_for_decision(decision, action_handler, str(event["thread_ts"]))
                ),
                thread_ts=event["thread_ts"],
            )
        except Exception:
            logger.exception("message handler failed")

    return app


def main() -> int:
    """Start the Socket Mode listener and block.

    Returns:
        A process exit code. Non-zero on a configuration problem, so systemd
        restarts rather than sitting there looking healthy.

    Raises:
        Nothing.
    """
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if not app_token:
        logger.error("SLACK_APP_TOKEN is not set; Socket Mode cannot connect")
        return 2
    try:
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        handler = SocketModeHandler(build_app(), app_token)
        logger.info("Gable is listening")
        handler.start()  # type: ignore[no-untyped-call]
    except RuntimeError:
        logger.exception("Gable could not start")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
