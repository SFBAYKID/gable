---
name: project-gable-decision-discipline
description: Gable's recurring failure mode is documentation drifting out of sync with code and with itself; the decision log is append-only
metadata:
  type: project
---

Gable's dominant recurring defect is **documentation drift**, not code defects. The
project has already lost days once to it (ARCHITECTURE.md described a Canva Bulk
Create export for days after the code moved to Google Slides), which is why
CLAUDE.md 2.7 now makes a same-commit doc sweep mandatory and the ARCHITECTURE.md
section 9 decision log append-only.

**Why it matters for review:** because the docs are the design of record, a review
that only reads code misses half the defects. Two documents disagreeing with each
other is a real finding of the same severity as a code bug — the next agent reads
the stale one and builds on it. Check ARCHITECTURE.md, STATUS.md, AGENTS.md and
CLAUDE.md against each other and against the code, not just the code.

**How to apply:** On every Gable review, explicitly diff the docs against each other
and against `src/`. Flag any doc claim about the code that is no longer true as a
finding, and note that the fix belongs in the same commit. Also check that every
`GABLE_*` variable the code reads is documented in `.env.example` — that is a
CLAUDE.md section 10 gate and it has been broken by in-flight edits before.

Watch for the inverse too: a decision-log row that records a constraint the code then
violates. The log is the authority; if code and log disagree, one of them is a bug
and the review must say which.

See [[user-chase]] and [[feedback-review-format]].
