---
name: user-chase
description: Chase owns Gable; backend/Python-strong, runs a critic agent against plans before any code is written
metadata:
  type: user
---

Chase (chase@monarchconnected.com) owns Gable and the Monarch Connected site. He is
strong on backend/Python and on operations; he is not a frontend designer. Carmen
(the designer) is the end user of Gable, not Chase.

He deliberately runs an adversarial review pass over a *plan* before implementation
starts — he brings a design he already intends to build and asks for it to be
attacked, listing the specific concerns he already suspects. Treat his stated
concern list as a floor, not a ceiling: he explicitly asks for "anything else you
find", and the highest-value findings have been the ones outside his list.

He supplies a VERIFIED FACTS block and asks that it not be re-verified. Honor that —
spend the effort on what is *not* in it. He also asks explicitly to be told where he
is about to build on an unverified assumption, which maps directly onto the
confidence-labelling rule in the repo's CLAUDE.md 2.2.

**His shipping bar, in his own words: "If it does not look correct in testing we can
not ship it to customers."** He demos Gable live to real customers by filling the real
Google Form with a real property address and watching Slack. So a run that ends in
`needs_review`, a silent 30-second gap, a duplicate post, or an unreplaced sample
name/headshot is a *product* failure to him, not a cosmetic one. Weight visual-output
and "does the whole sequence actually execute" findings above internal code quality —
and always separate "genuinely not implemented" from "implemented but not wired", since
those have very different costs the night before a demo.

See [[feedback-review-format]] and [[project-gable-decision-discipline]].
