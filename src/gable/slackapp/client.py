"""Construct Slack Web API clients with an application-owned time budget.

Slack's SDK defaults are not a delivery contract.  In particular, a default
connection-error retry can make one outbox write occupy two full HTTP timeout
windows while another process decides that its durable claim is stale.  Gable
therefore gives each Web API attempt one bounded transport window and lets the
SQLite outbox reconcile and retry durable messages at the application layer.

This module does not post, reconcile, or choose a channel.  It only makes the
transport behavior shared by Bolt and the one-row operator tool explicit.
"""

from __future__ import annotations

from typing import Final

from slack_sdk import WebClient

# One HTTP write must finish well inside the durable outbox's lease.  Keep this
# a literal application contract rather than inheriting the installed SDK's
# current 30-second default.
SLACK_HTTP_TIMEOUT_SECONDS: Final[int] = 10

# Durable question and outcome retries belong to ``pipeline.questions`` after
# a history reconciliation.  A hidden SDK retry would create a second write
# inside one claimed delivery attempt.
SLACK_TRANSPORT_RETRY_COUNT: Final[int] = 0


def build_web_client(token: str) -> WebClient:
    """Build one Slack client with a bounded, non-retrying HTTP transport.

    Args:
        token: Bot token already validated by startup configuration.

    Returns:
        A synchronous Slack Web API client whose individual request timeout is
        ten seconds and whose SDK transport performs no implicit retry.

    Raises:
        Nothing during construction.  Individual Web API methods retain the
        Slack SDK's documented exceptions.
    """
    # WebClient timeout/retry contract:
    # https://docs.slack.dev/tools/python-slack-sdk/reference/web/client.html
    return WebClient(
        token=token,
        timeout=SLACK_HTTP_TIMEOUT_SECONDS,
        retry_handlers=[],
    )
