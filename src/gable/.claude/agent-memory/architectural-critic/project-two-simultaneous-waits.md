---
name: project-two-simultaneous-waits
description: Gable's recurring defect class — one run `status` column asked to carry two simultaneous waits, so Gable asks for something it has no state to receive
metadata:
  type: project
---

Gable's most repeated architectural defect is **one `status` column being asked to
carry two simultaneous waits**. Four instances by 2026-08-20: the credential
remedy, the address ask, the `needs_review` photo replacement (2026-08-15), and
the Open House `needs_template` + photo ask (2026-08-20).

The shape is always the same: one batched Slack message asks for two things, one
of them wins `runs.status`, and every consumer that answers "is this run waiting
for X?" with a `status ==` comparison then refuses the answer to the other one.

**Why:** the one-batched-ask rule (Chase, 2026-08-13: "if a user has to go back
and forth 19 times they are just going to build it themselves") deliberately puts
two asks in one message, while the run state machine kept one status per run. The
batching is right; the single-slot state was not.

**How to apply:** whenever a change adds a new ask, a new refusal string, or a new
paused state, check every `run.status ==` / `status in {...}` site against the set
of states that can actually produce that ask. The remedy pattern Chase chose is a
separate durable fact on the run (`runs.awaiting_photo`, schema v14) rather than
flipping the status. When reviewing such a fix, verify that **every** consumer of
the old status proxy was updated, not just the two that were reported — see
[[reference-gable-awaiting-photo-consumers]].
