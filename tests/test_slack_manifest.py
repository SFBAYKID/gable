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


def test_no_slash_command_or_command_scope_is_exposed() -> None:
    manifest = _manifest()
    scopes = manifest["oauth_config"]["scopes"]["bot"]

    assert "slash_commands" not in manifest["features"]
    assert "commands" not in scopes


def test_unused_write_and_direct_message_capabilities_are_not_requested() -> None:
    manifest = _manifest()
    scopes = set(manifest["oauth_config"]["scopes"]["bot"])
    events = set(manifest["settings"]["event_subscriptions"]["bot_events"])

    assert scopes.isdisjoint(
        {"files:write", "reactions:write", "im:write", "im:history", "groups:history"}
    )
    assert events.isdisjoint({"message.im", "message.groups"})
    assert "interactivity" not in manifest["settings"]
