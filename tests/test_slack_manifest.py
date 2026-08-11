"""Structural checks for Slack capabilities the runtime depends on."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

MANIFEST = Path(__file__).resolve().parent.parent / "slack" / "manifest.json"


def _manifest() -> dict[str, Any]:
    """Read the checked-in Slack manifest as an object mapping."""
    return cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))


def test_photo_handoff_declares_private_file_read_access() -> None:
    scopes = _manifest()["oauth_config"]["scopes"]["bot"]
    assert "files:read" in scopes


def test_file_share_messages_are_delivered_to_socket_mode() -> None:
    events = _manifest()["settings"]["event_subscriptions"]["bot_events"]
    assert "message.channels" in events
    assert _manifest()["settings"]["socket_mode_enabled"] is True
