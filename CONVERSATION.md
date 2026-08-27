# Gable — conversation design

Moved out of `ARCHITECTURE.md` on 2026-08-27: that file was at the 800-line
ceiling again and this is its most self-contained section. `AGENTS.md`
describes what the runtime agent says; this describes why the conversation is
shaped the way it is. Nothing below is edited in the move.

## Conversation design

This is what makes Gable an agent rather than a scheduled job, and it is as much
of the product as the renderer.

### 4A.0 Thread ownership

An ordinary `message` event is not an invitation merely because it has a
`thread_ts`. Before a plain reply or shared photo is accepted,
`slackapp/routing.py` reads the root. Only a root Gable authored or a root where
Carmen or Chase explicitly mentioned Gable is owned; the bounded cache keys
that decision by channel and root timestamp.

Direct `app_mention` events bypass this check, but do not transfer ownership of
a Monarch Website Watcher thread. Gable-authored listing threads keep automatic
follow-ups and uploads. If Slack cannot return the root or identify Gable's bot
user, the lookup fails closed and Gable stays silent; an explicit mention still
works.
Every interaction is restricted to two stable Slack IDs; names are not authorization.

### 4A.1 Confirm before acting

Gable resolves each owned-thread turn with up to twelve recent prior messages
and persisted facts for that thread's listing. Ambiguity is still resolved by
asking, never by taking the likely reading.

> **Carmen:** update the image
> **Gable:** Did you mean the large property photo or the agent headshot?
> **Carmen:** the big one
> **Gable:** On it. Drop the new one here.

"Update the image" could mean the hero, the headshot, or one of three secondary
photos. A confirmed property-photo replacement keeps the current flyer intact
while the new upload re-enters every geometry and visual gate. A headshot change
waits on the human-owned `Head Shots` folder. Asking costs seconds; guessing can
produce a wrong post that looks right, the failure `AGENTS.md` §5 forbids.

The rule generalises: **when Gable does not know, it asks.** It never picks the
convenient reading of an ambiguous instruction.

### 4A.2 Show that it is working

Every response to a Slack user starts `assistant.threads.setStatus` immediately
(`slackapp/status.py`). Slack renders the native pulsing purple Gable treatment,
auto-opens a new mention's thread, and clears it when Gable replies. The method
is documented for channel apps with the existing `chat:write` scope.

The status says Gable is thinking at once, then hold tight at one second,
jittering at three, and bobbing and weaving at five. At six seconds personality
gives way to the caller's truthful stage, refreshed as photo or flyer work moves.
An explicit empty status is still sent on exit so a failure cannot strand it.

This covers initial mentions, plain follow-ups in an existing thread, edit
actions, and shared photos. A posted message, reaction, or edited placeholder is
not equivalent: none receives Slack's native purple treatment, and placeholders
can survive a failure. The automatic form poll has no user thread to attach to.

Two rules keep this from being noise:

- **Name the real stage where possible.** "Fitting the image to the template"
  tells Carmen more than "working", and if it stalls she knows where.
- **Never let personality obscure state.** A failure is reported plainly, in
  words, with what failed. The fun is in the waiting, never in the outcome.

### 4A.3 Tools, not a script

The Slack model is given bounded tools for explicit flyer edits, status,
clarification, and reloading a corrected source template. The listing pipeline
itself performs research and rendering in a fixed, auditable order.

That is what lets Carmen phrase an edit naturally while pure request builders
and exact-target checks decide whether it is safe. Ambiguous targets are a
question, and a missing or multiply matched element is never ranked or guessed.

### 4A.4 Never claim more than it did

From `AGENTS.md` §5, and it outranks everything above. Only a human-supplied
property photo is accepted. If verification did not run, say so. If something
failed, name what failed.

The failure mode to design against is Gable reporting confident success on a post
that is subtly wrong, and Carmen — trusting it after fifty good runs — shipping
it without looking.

---
