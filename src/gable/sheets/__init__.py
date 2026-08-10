"""Google Sheets access: the entire persistence layer for Gable.

There is no database. Three tabs — `Form Responses 1` (read only, never
written), `Agents` (the template map), and `Runs` (append-only log and
idempotency guard) — hold all state, so Carmen and Chase can inspect and correct
it without tooling (ARCHITECTURE.md 3).
"""

from __future__ import annotations
