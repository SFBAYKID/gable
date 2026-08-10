"""Frozen application settings, parsed once from the environment at startup.

Reads every `GABLE_*` variable documented in `.env.example` into a single frozen
dataclass, so no other module performs an `os.environ` lookup (CLAUDE.md 5.4).
Parsing failures are reported together, not one per run, so a half-configured
droplet fails on the first boot rather than on the first listing.

Assumes: `.env` is present locally and the process environment is authoritative
in production (systemd `EnvironmentFile`). Assumes nothing about secret values.

Does not handle: acquiring credentials, refreshing them, or validating that a
token actually works. That belongs to the client that uses it.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
