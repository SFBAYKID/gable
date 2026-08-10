"""Core domain types: Listing, AgentProfile, PhotoResult, RunRecord.

These are the only shapes that cross module boundaries. Every stage in
ARCHITECTURE.md section 4 consumes one and produces another, which keeps the
orchestrator readable and the stages independently testable.

`Listing` carries a `problems` list rather than raising on bad input: a missing
description must not take down a batch (ARCHITECTURE.md 4.2).

Assumes: `response_row_id` is derived from a stable hash of (timestamp, agent
email, address) and never from the sheet row number, because inserting or
sorting rows would silently reassign identities (ARCHITECTURE.md 3.3).

Does not handle: persistence. `gable.sheets.repository` maps these to and from
the Sheet.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
