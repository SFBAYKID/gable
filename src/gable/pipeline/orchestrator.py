"""Runs one listing through every stage, and one batch through the poll cycle.

Stage order follows ARCHITECTURE.md section 4: poll, normalize, verify, resolve
photo, look up template, export, deliver. Each per-listing run is wrapped so an
exception marks that row failed and the loop continues — one listing failing
must never stop the batch (ARCHITECTURE.md 6).

Hard ceilings live here, in code and not in a comment (AGENTS.md 7):
`GABLE_MAX_BATCH` listings per cycle, `GABLE_MAX_RETRIES` attempts per listing,
and `GABLE_MAX_IMAGE_CALLS_PER_LISTING` image-generation calls ever.

`needs_photo` and `needs_template` are paused, not failed. They wait for Carmen
indefinitely and are re-checked on `/gable run` (AGENTS.md 6).

Assumes: every state transition writes to `Runs` with a timestamp. A listing
whose state cannot be explained from that log is a bug.

Does not handle: Slack. It returns results; the caller delivers them, which is
what lets `cli.py` run the whole pipeline with Slack absent.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
