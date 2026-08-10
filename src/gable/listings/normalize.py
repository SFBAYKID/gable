"""Raw sheet row to `Listing`: trimming, parsing, and validation.

Lowercases the email, normalizes the phone to E.164, parses the price into both
a number and a display string, and title-cases the address.

Validation failures do not raise. They populate `Listing.problems` and let the
orchestrator decide (ARCHITECTURE.md 4.2), because one malformed row must never
stop a batch.

Assumes: US-format phone numbers and USD prices. Both are marked with
`# ASSUMPTION:` at the point of parsing once implemented.

Does not handle: network access. Every function here is pure and unit-tested.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
