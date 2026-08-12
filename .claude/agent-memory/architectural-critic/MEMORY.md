# Agent Memory — architectural-critic (Gable)

- [Chase — who he is and how he uses this agent](user_chase.md) — owns Gable, backend-strong, brings plans to be attacked before building.
- [Review format Chase expects](feedback_review_format.md) — severity-ranked, `state -> wrong outcome` scenarios, no report files.
- [Gable's decision & documentation discipline](project_gable_decision_discipline.md) — doc drift is the project's dominant defect; review docs, not just code.
- [Unwired seams between Gable modules](project_gable_unwired_seams.md) — modules ship before their callers; grep for callers and diff the seam before reading logic.
- [Layout auto-correction, rejected 2026-08-11](project_gable_layout_correction.md) — Slides measures boxes, not ink; per-template offsets die on Carmen's next export.
- [Slack thinking indicator](project_gable_slack_thinking_indicator.md) — setStatus takes chat:write, but assistant threads are DM-only; `ok` is not proof it rendered.
