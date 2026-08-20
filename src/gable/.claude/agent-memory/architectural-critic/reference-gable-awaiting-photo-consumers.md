---
name: reference-gable-awaiting-photo-consumers
description: Every place in Gable that answers "is this run waiting for a photo?" — the full consumer list a change to that fact must update
metadata:
  type: reference
---

`runs.awaiting_photo` (schema v14, added 2026-08-20) records whether the last ask
Gable sent included the property photograph. It is the fix for
[[project-two-simultaneous-waits]].

Every site that answers "is this run waiting for / may this run receive a photo?"
— verify each exists before recommending against it:

- `slackapp/photos.py` — `waiting_for_photo` disjunction (ingress acceptance)
- `slackapp/runtime.py` — `_needs_fresh_photo_upload`, called at the
  `build_with_blank_fields` and `rebuild_flyer` branches of `execute_action`
- `slackapp/recovery.py` — the abandoned-ingress re-ask filter
- `pipeline/resume_claim.py` — `PHOTO_RESUME_STATES` (gates the question-guarded
  claim *and* duplicate-delivery suppression)
- `slackapp/context.py` — `listing_context`, the only structured view the
  Anthropic brain gets; `intents._paused_status` regex-parses its status line
- `slackapp/editing.py` — `_status`, the `report_status` answer
- `db/photo_store.py` — `_pending_photo_row`, hard-coded to
  `q.target_status = 'needs_photo'`
- `tools/run_row.py` — `--resume --hero-photo-url` resume fields

Also note: `store.PAUSED`, `store.TERMINAL`, `RunRow.is_paused` and
`RunRow.is_terminal` exist, but several sites re-derive narrower literal sets by
hand (`resume.py` `may_rebuild`, `slackapp/batches.py`,
`pipeline/run_reporting.py`, `pipeline/resume_claim.py`). `is_terminal` has never
had a caller. When reviewing state-machine work, grep for the literal sets, not
just the helpers.
