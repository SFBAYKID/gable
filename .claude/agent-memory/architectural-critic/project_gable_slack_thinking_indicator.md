---
name: gable-slack-thinking-indicator
description: Slack's in-thread thinking indicator — assistant.threads.setStatus takes chat:write (not assistant:write), but assistant threads are documented as DM-container-only, so an `ok` response is not proof anything rendered.
metadata:
  type: project
---

`assistant.threads.setStatus` accepts **`chat:write`** as well as `assistant:write`
(docs.slack.dev say chat:write will soon be the only requirement). Gable already
has `chat:write`, so the scope was never the blocker — two rejected attempts were
built around the false belief that it was, and fell back to a static
`hourglass_flowing_sand` reaction nobody noticed.

**Why:** the open question is not the scope, it is the *surface*. Slack's own
"developing AI apps" guide puts assistant/agent threads in the app's DM /
Messages tab and requires `agent_view` (or legacy `assistant_view`) in the app
manifest. Gable's `slack/manifest.json` declares neither, and Gable's threads are
in a public channel (`C0BP597644B` / `C0B02721MNK`). A Slack method returning
`ok` means the arguments were accepted, **not** that a human saw anything —
accepted-and-rendered-nowhere is a live possibility here and only a person
watching the thread can rule it out.

**How to apply:** when any agent reports the spinner "verified live because the
call returned ok", that is not evidence. Demand: which channel id, was it a
channel thread or a DM, and did a human watch the thread. Record the answer in
`CLAUDE.md` §4.3 with the channel type named. The same rule generalises — for
Slack decoration APIs, `ok` proves acceptance, never rendering.

Alternatives if it turns out not to render in channel threads: an animated custom
emoji GIF is the only other genuinely animating in-thread affordance, and it
collides head-on with the no-emoji house style in `AGENTS.md` §2.0 — that
collision is Chase's call, not the building agent's. Related:
[[gable-decision-discipline]], [[gable-unwired-seams]].
