"""Socket Mode bootstrap and the poll loop.

Socket Mode opens an outbound WebSocket: no inbound ports, no TLS certificate,
no domain on a $4 droplet. The tradeoff is that no Apps Script webhook can reach
Gable, so the Sheet is polled every `GABLE_POLL_INTERVAL_SECONDS`
(ARCHITECTURE.md 2.2).

Assumes: `slack_bolt` reconnects on its own after a disconnect. Gable logs it
and never exits (ARCHITECTURE.md 6).

Does not handle: business logic. Handlers parse and delegate (CLAUDE.md 5.4).
The poller and the Slack handler both reach the pipeline, so the race between
them is guarded here, not in the pipeline.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
