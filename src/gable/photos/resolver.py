"""The photo cascade and the policy gate.

Walks each source in order and returns the first `PhotoResult` at or above
`GABLE_PHOTO_MIN_CONFIDENCE`. A candidate below the threshold routes to "ask
Carmen" — never to the next source, and never onto a flyer. A flyer with no
photo gets caught; a flyer with the wrong house ships (ARCHITECTURE.md 4.4).

Every result records provenance. `photo_source` in `Runs` is the audit trail for
how a picture ended up on a flyer, not decoration.

Assumes: source adapters share one signature so they can be reordered or
disabled by configuration without editing the resolver.

Does not handle: generation itself. It decides whether generation is permitted;
`enhance.py` and the image provider carry it out, always tagged
`ai_disclosure: app_generated`.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
