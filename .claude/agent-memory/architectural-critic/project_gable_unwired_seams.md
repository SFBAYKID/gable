---
name: project-gable-unwired-seams
description: Gable modules are built ahead of the code that calls them, so contract mismatches between modules go undetected — always grep for callers and test the seam
metadata:
  type: project
---

Gable is deliberately built module-first: pure functions land complete, with full tests,
**before anything calls them**. That is a good practice for testability and it is why the
renderer was finished before a Google credential existed. It has one systematic cost that
has now produced real defects.

**The failure mode:** two modules get written against contracts that contradict each
other, and nothing detects it, because (a) no code calls either one yet, and (b) every
test is a within-module test. Observed 2026-08-10: `photos/store.py` returns an `http://`
URL while `slides/renderer.py:validate_image_url` raises on anything not `https://`, and
`config.py` validates the same rule. Both modules had passing tests and a clean
`mypy --strict`. Also observed: a live-verified fix applied to one function
(`resize_element`) while the identical bug sat 40 lines above it in `scale_element`.

**Confirmed again 2026-08-11, at larger scale.** With 717 tests green and mypy clean,
`build_runner` and `Poller` had **zero production callers** — the only process entry point
started the Slack listener and nothing else. The pipeline that STATUS.md described as
"runs end to end" had never run end to end outside a test harness or a hand-driven tool.
Green tests said nothing about it because every seam was stubbed by an injected lambda.

**Why it matters:** the test suite's green is a statement about functions, not about the
system. A defect at a seam survives every gate in CLAUDE.md section 10 until the first
wired run, which on this project means the first real listing.

**How to apply:** On any Gable code review, before reading a module's logic —
1. `grep` for callers of every public function. Zero callers means the contract is
   unvalidated, and any claim about how it fits the pipeline is an assumption. Do this for
   entry points too: find what `ExecStart` actually launches and read only that call tree.
2. Diff its inputs/outputs against the module on the other side of the seam (URL scheme,
   units, pixel-vs-EMU, filename suffix, config variable names, **dict key names** — the
   `price` vs `list_price` split between `intake.PUBLIC_FACTS` and `enrich.Facts.as_dict`
   is this exact bug and causes an unbounded re-research loop).
3. A `Callable` field on a dataclass with a no-op default is an unvalidated seam. Every
   test that leaves the default in place proves nothing about the live path. Check which
   defaults `pipeline/live.py` actually overrides.
4. When a bug is found and fixed from a live run, grep the file for the same pattern
   elsewhere before accepting the fix as complete.
5. Ask which config variable feeds each new dataclass. A frozen-config dataclass with no
   loader is a module that cannot be wired (CLAUDE.md section 5.4).
6. **Check the status/idempotency asymmetry.** `db/store.py` splits statuses into
   TERMINAL and PAUSED, but `has_been_handled` only tests TERMINAL, so every non-terminal
   exit (`needs_photo`, `needs_review`, `needs_info`) re-enters the poller on the next
   pass — re-spending, re-posting, re-copying Drive files forever. Any new status must be
   classified in both places, and pausing needs a re-ask suppressor.
7. Threading: `db/schema.py:connect` omits `check_same_thread=False` and
   `Poller.run_forever` calls `signal.signal`, which raises off the main thread. Both
   detonate the first time the poller and the blocking Slack listener share a process.

See [[project-gable-decision-discipline]] and [[feedback-review-format]].
