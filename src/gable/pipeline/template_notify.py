"""Deliver one measured source-template verdict into Slack, exactly once.

Split out of `template_triage.py` on 2026-08-26 when that file reached the
800-line ceiling. The seam is real rather than arithmetic: everything here is
about getting an already-decided sentence into Slack durably, and nothing here
decides what the sentence should be.

Assumes: the verdict is already persisted. Does not handle: measuring a design.
"""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection
from uuid import UUID, uuid5

from gable.db import store
from gable.pipeline.questions import (
    PostOnce,
    ReconcilePost,
    notification_guard,
    post_persisted_notification,
)

_TEMPLATE_NOTIFICATION_NAMESPACE = UUID("a5f075c1-55c9-46b8-893b-5326a29e4d87")


def _notification_key(file_id: str) -> str:
    """Return the process-local serialization key for one source template."""
    return f"template:{file_id}"


def _notification_client_id(audit: store.TemplateAudit) -> str:
    """Derive one stable Slack identity from the exact persisted revision."""
    identity = "\0".join((audit.file_id, audit.modified_time, audit.checked_at, audit.summary))
    return str(uuid5(_TEMPLATE_NOTIFICATION_NAMESPACE, identity))


def deliver_template_notification(
    connection: Connection,
    audit: store.TemplateAudit,
    say: Callable[[str, str | None], str],
    *,
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> bool:
    """Deliver one exact pending template verdict and confirm it atomically."""
    with notification_guard(_notification_key(audit.file_id)):
        current = store.template_audit(connection, audit.file_id)
        if (
            current is None
            or not current.notification_pending
            or current.modified_time != audit.modified_time
            or current.summary != audit.summary
            or current.checked_at != audit.checked_at
        ):
            return False
        posted_ts = post_persisted_notification(
            current.summary,
            current.slack_thread_ts or None,
            _notification_client_id(current),
            current.checked_at,
            current.notification_attempted_at,
            current.notification_attempt_count,
            say,
            claim=lambda expected_count, stale_before: store.claim_template_notification_delivery(
                connection,
                current,
                expected_count,
                stale_before,
            ),
            release=lambda token: store.release_template_notification_delivery(
                connection,
                current,
                token,
            ),
            post_once=post_once,
            reconcile=reconcile,
        )
        return store.confirm_template_notification(connection, current, posted_ts)


def drain_template_notifications(
    connection: Connection,
    say: Callable[[str, str | None], str],
    post_once: PostOnce | None = None,
    reconcile: ReconcilePost | None = None,
) -> int:
    """Attempt each stored template verdict without repeating its inspection."""
    return sum(
        deliver_template_notification(
            connection,
            audit,
            say,
            post_once=post_once,
            reconcile=reconcile,
        )
        for audit in store.pending_template_notifications(connection)
    )
