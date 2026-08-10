"""Block Kit builders for every message shape in AGENTS.md section 2.

Pure functions: a `Listing` plus a `PhotoResult` in, a block list out. No
network, fully unit-testable.

The AI-generated warning in AGENTS.md 2.3 is never softened, shortened, or
dropped under any policy setting. If a synthetic image reaches a flyer, the
record of that must be impossible to miss.

Assumes: Block Kit's documented JSON schema; every builder is checked against
the shapes in AGENTS.md 2.

Does not handle: posting. `handlers.py` and `app.py` own the client.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
