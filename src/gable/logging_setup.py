"""Structured JSON logging with mandatory secret redaction.

Redaction is a filter installed on the root logger, not a convention that every
call site has to remember (CLAUDE.md 3, ARCHITECTURE.md 7). Anything matching a
known token shape — `xoxb-`, `xapp-`, service-account JSON bodies, Spaces keys,
Bearer headers — is replaced before a record reaches a handler.

Assumes: journald consumes stdout in production; `LOG_FORMAT=console` is for
local work only.

Does not handle: log shipping, rotation, or sampling. journald owns those.

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
