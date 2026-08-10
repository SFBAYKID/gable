"""Source adapters for the photo cascade: form upload, Drive, brokerage, web.

Each adapter takes a `Listing` and returns `PhotoResult | None` with a
confidence score and a provenance tag. Uniform signature, no shared state, one
unit test per adapter with the network mocked.

Assumes: address matching is fuzzy and must produce a confidence, not a boolean.

Does not handle: scraping Zillow or any site whose terms forbid it. The agent's
own brokerage site is the preferred source and the safest rights position
(CLAUDE.md 8).

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
