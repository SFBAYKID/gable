---
name: feedback-review-format
description: How Chase wants architectural reviews delivered — severity-ranked, concrete failure scenarios, no report files
metadata:
  type: feedback
---

Deliver reviews as: severity-ranked findings, each with a concrete failure scenario
written as `inputs/state -> wrong outcome`, and an explicit callout wherever the plan
rests on an unverified assumption. Do not write findings to a `.md` report file —
return them in the response body.

**Why:** Chase reviews plans before building and needs to triage what blocks the
build from what can wait. A finding phrased as a principle ("this is fragile") is not
actionable; a finding phrased as "99 existing rows + no Runs tab -> 99 renders on
first poll" tells him exactly what to change. The repo's own CLAUDE.md 2.2 requires
every technical claim to be classifiable as verified / assumption / recommendation /
needs-verification, so an unlabelled assertion is a rule violation, not a style
choice.

**How to apply:** On any Gable plan or design review. Separate what was read in
vendor documentation from what was inferred, and say which. When a claim about an
external API is load-bearing for the design, go read the vendor doc rather than
recalling it — the answers have repeatedly changed the recommendation (e.g. Drive's
export cap and Slides' batchUpdate atomicity both flipped parts of a verdict).

See [[user-chase]] and [[project-gable-decision-discipline]].
