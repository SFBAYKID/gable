"""Local entry point — runs the pipeline with Slack entirely absent.

This is how Phase 1 is developed and tested before a live workspace exists
(CLAUDE.md 6). It drives `gable.pipeline.orchestrator` directly and writes the
Bulk Create file to disk instead of posting it.

Assumes: credentials for Sheets and photo hosting are present in `.env`; Slack
credentials are not required.

Does not handle: the poll loop or Socket Mode. Those live in `gable.slackapp.app`.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
