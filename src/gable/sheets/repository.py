"""Tab-level reads and writes, plus the idempotency guard.

Maps `Form Responses 1` rows to raw dicts, `Agents` rows to `AgentProfile`, and
`RunRecord` back to an appended `Runs` row. Before any listing is processed,
`Runs` is checked for a terminal status against `response_row_id` — without that
check every poll rebuilds every flyer (ARCHITECTURE.md 3.3).

Assumes: tab names come from config and match the live workbook; the header row
is row 1 and is stable.

Does not handle: mutation of `Form Responses 1`. Gable appends to `Runs` and
reads everything else, always (CLAUDE.md 11).

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
