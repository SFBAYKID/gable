"""Slash commands, block actions, and mentions.

Handlers parse and delegate — no business logic lives here (CLAUDE.md 5.4).
Commands are listed in AGENTS.md section 3; buttons are Approve, Replace photo,
and Skip.

Assumes: an action payload identifies its listing by `run_id` carried in the
block `value`, so a click stays correct even after the message scrolls away.

Does not handle: replying to anyone outside `GABLE_SLACK_CHANNEL_ID`, or
answering questions outside Gable's own state and data (AGENTS.md 3).

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
