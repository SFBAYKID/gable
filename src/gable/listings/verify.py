"""Cross-check submitted agent details against the brokerage site via Firecrawl.

Verification advises; it never overwrites. If the form says `jon@example.com`
and the site says `john@example.com`, Gable flags the discrepancy in Slack and
uses the form value — silently "correcting" a contact detail is how a flyer
ships with a phone number nobody answers (ARCHITECTURE.md 4.3, AGENTS.md 4.7).

Results are cached per `brokerage_url` for `GABLE_VERIFY_CACHE_HOURS`, capped at
one call per unique URL per 24 hours (AGENTS.md 7).

Assumes: Firecrawl returns page text; no assumption is made about its exact
response envelope until it has been observed.

Does not handle: a Firecrawl outage as a fatal error. Verification is skipped,
the listing is flagged, and the run continues (ARCHITECTURE.md 6).

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
