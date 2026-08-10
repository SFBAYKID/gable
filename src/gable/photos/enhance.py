"""Enhancement of a REAL retrieved photo — never generation.

Exposure, straightening, upscaling, and sky replacement are permitted under
every policy except `no_ai`. Generation lives on a separate code path behind the
policy gate in `resolver.py`, and the two must never share a function
(CLAUDE.md 8).

Assumes: the input is a real photograph of the listed property, already resolved
above the confidence threshold.

Does not handle: loading a full-resolution image into memory. Images stream to
disk — the droplet is 512MB-class (CLAUDE.md 9).

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
