"""Thin authenticated wrapper over the Google Sheets API.

Owns credential loading, the API client handle, timeouts, and bounded retry with
jittered backoff. It exposes range reads and appends; it knows nothing about
listings.

Assumes: a service-account JSON key at `GOOGLE_SERVICE_ACCOUNT_FILE`, and that
the workbook has been shared with that account's `client_email` explicitly — it
does not inherit Chase's access (README.md setup step 1).

Does not handle: tab semantics, idempotency, or schema. See `repository.py`.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
