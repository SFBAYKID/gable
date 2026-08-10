"""Builds the Canva Bulk Create file for a batch of ready listings.

One file per batch, not per listing — Bulk Create is built for batches and one
upload beats six (ARCHITECTURE.md 4.7). Column headers must stay stable and
documented: renaming one silently breaks Carmen's saved field connections.

BLOCKED ON SPIKE A. Whether an uploaded CSV/XLSX can carry an image URL into a
Bulk Create image column is unverified (CLAUDE.md 4.3 item 1). Image columns are
confirmed to exist in Bulk Create's *manual* data-entry table; that observation
does not extend to uploaded files. This module's entire output format depends on
the answer, so it stays empty until Chase runs the spike.

Assumes: nothing yet, by design.

Does not handle: uploading to Canva. There is no such API on the Teams plan;
Carmen uploads the file herself.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates this module directly.
"""

from __future__ import annotations
