"""Read every Gable thread in the channel and flag the ones that went wrong.

Every defect in the decision log was found the same way: Chase read a Slack
thread and saw something wrong. `tools/audit_experience.py` measures the run
history; this measures the thread itself, which is what a person lived
through. A listing whose thread holds more than three Gable messages, or the
same Gable sentence twice, or no flyer link at the end, is flagged whatever
the database says about it.

Read-only. It calls `conversations.history` and `conversations.replies` with
the configured bot token and posts nothing. Run it on the droplet, where the
token is Gable's, against the configured channel; set
`GABLE_SLACK_CHANNEL_ID` on the command line to audit the playground instead.

Does not handle: judging whether a flyer looks right, or reading channels the
bot is not in.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Final

from gable.config import ConfigError, Settings
from gable.slackapp.client import build_web_client

#: More Gable messages than this in one listing thread and the listing has
#: stopped being worth the automation: announcement, one ask, one link.
MESSAGE_BUDGET: Final[int] = 3
#: Slack pages, so a runaway channel cannot make this loop forever.
MAX_PAGES: Final[int] = 20
PAGE_SIZE: Final[int] = 200


@dataclass(frozen=True, slots=True)
class ThreadReport:
    """What one Gable-owned thread looked like to the person in it."""

    thread_ts: str
    title: str
    gable_messages: int
    repeats: tuple[str, ...]
    ends_with_link: bool
    escalated: bool
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def flagged(self) -> bool:
        """Whether a person would have read this thread as Gable failing."""
        return bool(self.problems)


def _folded(text: str) -> str:
    """One message compared as words."""
    return " ".join(text.split()).casefold()


def _is_gable(message: dict[str, Any], bot_id: str, bot_user_id: str) -> bool:
    """Whether Slack attributes a message to Gable's own bot."""
    return (bool(bot_id) and str(message.get("bot_id") or "") == bot_id) or (
        bool(bot_user_id) and str(message.get("user") or "") == bot_user_id
    )


def audit_thread(
    messages: list[dict[str, Any]],
    bot_id: str,
    bot_user_id: str = "",
) -> ThreadReport:
    """Judge one thread from its messages, root first.

    Args:
        messages: The root message and every reply, oldest first, as Slack
            returns them from `conversations.replies`.
        bot_id: Gable's bot id, from `auth.test`.
        bot_user_id: Gable's user id, from `auth.test`.

    Returns:
        A `ThreadReport` naming each problem in plain words. Empty problems
        means the thread read the way it should: one announcement, at most
        one ask, and a flyer link at the end.

    Raises:
        Nothing.
    """
    if not messages:
        return ThreadReport("", "", 0, (), False, False, ("empty thread",))
    root = messages[0]
    title = " ".join(str(root.get("text") or "").split())[:80]
    gable = [m for m in messages if _is_gable(m, bot_id, bot_user_id)]
    texts = [str(m.get("text") or "") for m in gable]
    # The announcement is the root; count what followed it.
    replies = texts[1:] if messages and _is_gable(root, bot_id, bot_user_id) else texts
    seen: dict[str, int] = {}
    for text in replies:
        seen[_folded(text)] = seen.get(_folded(text), 0) + 1
    repeats = tuple(
        " ".join(text.split())[:80] for text in dict.fromkeys(replies) if seen[_folded(text)] > 1
    )
    last = texts[-1] if texts else ""
    ends_with_link = "docs.google.com/presentation" in last
    escalated = any("will not ask again" in text for text in texts)
    problems: list[str] = []
    if len(replies) > MESSAGE_BUDGET:
        problems.append(f"{len(replies)} Gable messages after the announcement")
    if repeats:
        problems.append(f"the same sentence {len(repeats)} time(s) over")
    if escalated:
        problems.append("Gable said it was stuck and stopped asking")
    if not ends_with_link:
        problems.append("no flyer link at the end")
    return ThreadReport(
        thread_ts=str(root.get("ts") or ""),
        title=title,
        gable_messages=len(replies),
        repeats=repeats,
        ends_with_link=ends_with_link,
        escalated=escalated,
        problems=tuple(problems),
    )


def _paged(call: Any, key: str, **arguments: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Follow Slack cursors for one list-returning method, within the page cap."""
    found: list[dict[str, Any]] = []
    cursor = ""
    for _page in range(MAX_PAGES):
        if cursor:
            arguments["cursor"] = cursor
        response = call(**arguments)
        page = response.get(key, []) if response else []
        found.extend(item for item in page if isinstance(item, dict))
        cursor = str((response.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor:
            break
    return found


def audit_channel(client: Any, channel: str, days: int) -> list[ThreadReport]:  # noqa: ANN401
    """Audit every Gable-rooted thread in a channel over the last `days`.

    Args:
        client: A Slack WebClient authenticated as Gable.
        channel: The channel id to read.
        days: How far back to look.

    Returns:
        One report per thread Gable opened, newest first.

    Raises:
        Exception: Slack SDK errors propagate; a read that cannot run is not
            a clean audit.
    """
    identity = client.auth_test()
    bot_id = str(identity.get("bot_id") or "")
    bot_user_id = str(identity.get("user_id") or "")
    oldest = str(time.time() - days * 86400)
    roots = _paged(
        client.conversations_history, "messages", channel=channel, oldest=oldest, limit=PAGE_SIZE
    )
    reports: list[ThreadReport] = []
    for root in roots:
        if not _is_gable(root, bot_id, bot_user_id) or not root.get("reply_count"):
            continue
        replies = _paged(
            client.conversations_replies,
            "messages",
            channel=channel,
            ts=str(root.get("ts") or ""),
            limit=PAGE_SIZE,
        )
        reports.append(audit_thread(replies, bot_id, bot_user_id))
    return reports


def render(reports: list[ThreadReport]) -> str:
    """One line per thread, flagged ones first, for a person to read."""
    lines: list[str] = []
    for report in sorted(reports, key=lambda item: (not item.flagged, item.thread_ts)):
        mark = "FLAG" if report.flagged else "ok  "
        detail = (
            "; ".join(report.problems) if report.problems else "one ask or fewer, link at the end"
        )
        count = f"{report.gable_messages:>2} msgs"
        lines.append(f"{mark} {report.thread_ts} {count}  {report.title[:60]!r}: {detail}")
    flagged = sum(report.flagged for report in reports)
    lines.append(f"{len(reports)} thread(s), {flagged} flagged")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Read the channel and print the audit; exit 1 when any thread is flagged."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=7, help="how far back to read (default 7)")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"configuration problem: {exc}", file=sys.stderr)
        return 2
    client = build_web_client(settings.slack_bot_token)
    reports = audit_channel(client, settings.slack_channel_id, args.days)
    print(render(reports))
    return 1 if any(report.flagged for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
