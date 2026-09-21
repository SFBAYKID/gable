# Gable — status, and what's needed from Chase

Last updated 2026-09-21 by the building agent.

## 2026-09-21 — two #calvo threads that could not be answered, and why

Chase brought two threads from #calvo. Both are the same shape: Carmen did
exactly what Gable asked, and Gable asked again. Both are fixed, both are
reproduced in tests, and one of them reverses a decision row — read that row
before touching this again.

**Melanie Humeniuk, now Melanie Kim — Open House.** Gable paused on a
name mismatch. Carmen replied "I just found out she changed her name. It's been
updated everywhere. Rerun." Gable then paused on "the official Corner House
Realty website has no exact profile for this agent" and told her to correct the
request or Agents Contact Information — the two things she had just corrected,
and the two things that cannot reach that website.

Verified live today through the site's own page search: `search=Melanie Kim`
returns `[]`, `search=Melanie Humeniuk` returns her profile and her open-houses
twin, both still under the previous name. The site lags the roster because
people edit it on their own schedule. The decisive detail is that the identical
row builds fine on every design that does **not** print a credential, because
the phone cross-check yields to the workbook when no profile comes back — the
ask side and the build side were reading one answer two different ways, which
is the rule 4.3 item 15 already proved once. A complete roster row plus
`GABLE_DEFAULT_AGENT_CREDENTIAL` is now enough whether the site is silent or
merely has no page. An agent with no filed row, a row with no direct phone, and
an empty credential setting all still stop, and all three are tested.

**Ian DePinto — Client Review Post.** Gable asked for the review quote. Carmen
pasted it. Gable asked again. She pasted it again; Gable said "I picked this
listing back up, but the run did not produce an outcome I could report." She
put the value in the spreadsheet and said "Please rerun"; Gable said the same
sentence again. Four defects stacked:

1. `review_values` read both halves of a review or neither, so his review — a
   Google export, 415 readable characters signed with the username "lucyglou" —
   lost its quote because the name was unreadable.
2. `for_intake` accepted a *stated* review quote only when a client name was
   already known, so every copy Carmen sent was stored and then discarded.
3. `review_quote` is the first field that design resolves and preflight asked
   about only the first missing field, so the reviewer's name was never reached
   and never asked for. Answering correctly could not move the run in either
   direction.
4. When `repeat_guard` withheld the third copy of the question, the caller
   could not tell that decision from a run that fell over, and answered both
   with a sentence that names nothing.

Each half of a review is now kept on its own merits, a stated quote is recorded
like every other stated value, preflight names every missing section in one
ask, and a rerun on an escalated thread says what it re-read and that Chase
already has it. A nameless quote still never reaches a flyer: preflight blocks
on the empty client name instead, which is a question Carmen can answer. Walked
end to end after the change — the thread that never converged now converges in
one round trip, and Carmen answering both halves in one reply converges in
zero.

**Rehearsed in the playground, against both real agents.** Two `Testing_1`
rows, run from a staging copy on the droplet with the channel override, so the
listener kept serving production throughout and `/opt/gable/.env` was never
edited. Melanie Kim's Open House was run once against the *deployed* code first
— by accident, `tools/run_row` has no `sys.path` shim and needs `PYTHONPATH`,
which is the trap `TESTING.md` §0e already warns about — and it reproduced the
production refusal word for word. Against the fix she gets past the contact
gate, her headshot publishes, and the thread asks for the photographs and the
price. Ian DePinto's Client Review Post, seeded with his real Google-export
review, asked for the **client name** — the half nobody has — and built on the
answer. Read back off the Slides file: the flyer carries his real review text
and "Sharon", with no sample content left. `tools/audit_threads` reads his
thread `ok`; hers flags only for having no flyer link, which is correct, since
she is legitimately waiting on photographs.

The reviewer name in that rehearsal was recorded directly rather than typed
into Slack, because a scripted post carries a `bot_id` and `routing.py` drops
it. So the *ask* was rehearsed live and the *reply* was not.

**A standing guard, and three more instances it caught.** Chase asked that
this class of mistake stop happening, so the rule is now a test over
`store.SUPPLIABLE_FIELDS` rather than a fix per field: record one answer,
alone, and it must reach the value map. It failed immediately on `beds`,
`baths` and `square_feet` — all three are named in the batched ask, all three
were stored by `record_stated`, and nothing read them back. Answering "3 beds,
2 baths, $600,000" delivered the price and asked again for the beds and baths.
That was live on the commonest ask there is. Fixed in the same commit.

**Found while rehearsing:** every run that asks for a row of property
photographs was logging `recorded a photo ask that its message does not carry`
while carrying one. The guard listed two phrasings and the 2026-09-20 row ask
is a third. Fixed in the same commit; see the decision row.

**Both #calvo threads are still open.** Nothing was posted there from here.
Once this is deployed, each needs a "rerun" in its own thread to pick up the
fix — Melanie's should then ask for photographs, and Ian's for the reviewer's
name.

## 2026-09-20 — three property photos on the designs that draw three

**What Carmen asked for, and what actually happened.** She uploaded three
photos to a New Listing with Open House thread and said "Make the first photo
the main photo on the graphic." Gable refused them: the handler had a hard
one-image rule, and `brain.py` was instructed to answer such a request by
saying it could only place the large one. The same request on 2026-08-28 got a
worse answer — "I'll use the road as the large photo and the other two in the
smaller photo spots", which was never possible — and Carmen said "Perfect!".

**What is built now.** The row of smaller wells is measured live on every
build; the ask names how many spaces the design has; a batch of two or three
replaces every measured well at its exact size and transform in one atomic
batch, with a geometry readback; and the placed batch is stored so a rebuild
reuses it. Three designs have a certified row — New Listing, New Listing with
Open House, Open House — measured against all six live designs. Gable never
reads the main photograph out of the pictures; a number settles it, and
anything else retains the uploads and asks for the number without letting go
of the listing.

**Two things for Chase, neither of them blocking.**

1. **A single photograph still leaves the design's own sample pictures in the
   smaller wells.** That is today's behaviour on every New Listing and Open
   House flyer and this change does not touch it, because changing it changes
   every flyer Carmen already reviews and that is a product call. A supplied
   batch does empty a well it has no photograph for, on the reasoning that a
   stranger's living room beside two real ones reads as finished and is wrong.
   If the same should apply when only one photo arrives, say so and it is a
   small change.
2. **Rounded photo corners are not preserved, and cannot be through this API.**
   The wells are `CUSTOM` shapes; `presentations.get` returns them with an
   empty `shapeBackgroundFill` and no path, and `createImage` has no mask or
   radius. The main photograph has therefore rendered square on every flyer
   built to date, including the ones you confirmed. Filling the row makes all
   three square and consistent instead of one square beside two rounded
   samples. Baking corners into a PNG alpha channel would work but needs a
   radius nothing reports.

**The photo work has been through the visual judge**, on the second attempt.
Every rehearsal before that delivered with "I could not complete the visual
inspection", which I reported as an exhausted budget. It was not: the ledger
holds $59.49 against a configured ceiling of $500, with roughly $440 free.
`tools/rehearse_property_photos.py` simply never called `spend.configure_ceiling`,
so it kept the module's $50 default while the droplet is set far higher. Fixed;
the judge now runs, and on a three-photo New Listing with Open House it raised
nothing about the photographs — only the price box, which is deliberately empty
and which Gable's own next sentence explains.

**Production was never affected.** `slackapp/runtime.py` configures the ceiling
at startup, so the live service has always had the full $500.

**Cancellation is still not built.** "Cancel this" still gets an acknowledgment
and no action; it was looked at during this work and deliberately left alone,
since it is a separate tool and a separate decision. It stays on the list below.


## Standing decision — change nothing and watch, 2026-09-01 to 2026-09-08 (ended)

This window has passed. It is kept because the list of what to run daily and
what counts as a flagged thread is still how a live change is judged.


Chase's call at the end of 2026-09-01: **no code changes for a week.** The
seven mechanisms below are deployed; the week is the measurement of whether
the bug rate actually dropped. What to do during it:

- **Daily:** run the thread audit against #calvo and read anything it flags.
  ```
  ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
    "cd /opt/gable && sudo -u gable ./.venv/bin/python -m tools.audit_threads --days 1"
  ```
  A flagged thread is read and classified — real input, stop, two readers,
  repeat, or something new — and written down here. Not fixed yet.
- **On any design edit:** the canary build reports in the design's thread by
  itself. Nothing to run.
- **Do not:** deploy, resume a run by hand, or edit a template to test
  something. A run that stalls is evidence; note it and leave it.

**Open after the week, in order.** A reply corpus for the conversation layer
(every real sentence typed to Gable, replayed through the model's decision
step with the expected tool frozen — the one class with no net under it); a
cancel tool, since "cancel this" currently gets an acknowledgment and no
action; scheduling the audit and the corpus refresh on the droplet, which
needs a decision on where the weekly summary posts; and Phase 2 from
CLAUDE.md §7.

**Still waiting on Chase:** confirm or reverse the silent-website credential
fallback (emptying `GABLE_DEFAULT_AGENT_CREDENTIAL` reverts it).

**Known stale or thin, noted rather than fixed during the freeze:**

- `AUDIT_2026-08-12.md`, `AUDIT_2026-08-13.md` and `ASK_CARMEN.md` are
  point-in-time documents from August and describe behaviour that has since
  changed; they are history, not instructions. `CONVERSATION.md` was on this
  list until 2026-09-20 and is current again: §4A.1a now describes the
  main-photo question, and §4A.4 says the reported photo count comes from what
  placement verified.
- `TEMPLATE_ISSUES.md`, `TEMPLATE_WIDTHS.md` and `TEMPLATE_CERTIFICATION.md`
  predate the canary build and the 2026-08-26 blocker kinds; the measurements
  in them are the ones the code carries, but their prose about what refuses a
  listing is older than the stop policy.
- The playground holds three test artifacts from today (`Testing_1` rows 534
  and 535, and one canary report) and the Gable drive holds their two flyers.
  Left on purpose so the runs stay auditable.


## 2026-09-01 — "How do we ensure it stops?": seven changes, all deployed, all rehearsed in the playground

Chase, after the two Under Contract threads: "This keeps happening, how do we
ensure it stops?" The honest diagnosis was three causes, not bad luck: Carmen
was the test harness, Gable was built to stop and each stop was being removed
one at a time after it hurt, and several facts had two readers that drifted.
Seven changes, each with its own commit and decision-log row:

1. **A real-address corpus.** Every address the form has ever received is
   replayed through the runner's own reader (`tools/refresh_address_corpus.py`,
   `tests/test_address_corpus.py`). Reviewing the first corpus found three more
   defects in an afternoon — "802 Dressage Ct" printed as "Dressage CT", a
   trailing slash hiding a ZIP, and 18 of 140 rows asked about for one missing
   comma the manifest already knew how to add on only one of two paths. 102 of
   140 real addresses now read as whole, up from 88.
2. **A canary build on every added or edited design.** `pipeline/canary.py`
   builds a test flyer with sample values, a sample photograph and face, runs
   the same readback, fitting and layout checks a listing gets, reports in the
   design's thread, and trashes the copy. Run live against Under Contract from
   the playground: built, found nothing wrong (Carmen had fixed the well),
   posted, copy confirmed in the Drive trash.
3. **A repeat guard.** A run never says the same sentence twice: the second
   time is one escalation naming Chase, the third is silence. Proven live on
   `Testing_1` row 534, a two-property address: ask, one re-ask with the photo
   held, escalation, then nothing — three Gable messages, run still paused.
4. **The stop policy.** A flyer that exists is sent. Only four stops survive
   after the copy, all about a fact, listed in ARCHITECTURE.md §4.7b. Proven
   live on row 535: delivered with the vision pass's note that the sample
   photo is a striped placeholder, one message, link at the end.
5. **No swallowed failures.** A repo standard walks every except handler.
6. **Rehearse before deploy.** CLAUDE.md §10 and TESTING.md §0d.
7. **A thread audit.** `tools/audit_threads.py` read the playground after the
   runs above and flagged exactly the escalated thread and passed the delivered
   one.

Also today, fixed and deployed before this build: the condo-as-two-properties
defect, the headshot audit charging the design's overhang to Gable, a built
flyer withheld over twenty points, a swallowed website timeout, and the manual
resume ignoring a stated address. Both stuck listings were delivered.

**One correction to own.** Two commits went to `main` mid-afternoon with two
docstring lint findings and without their decision rows, because a gate piped
into `tail` could not fail the chain. Fixed in the next commit; every chain
since runs with `set -o pipefail`.

**What I need from Chase:** nothing blocking. The silent-website credential
fallback from this morning remains the one judgment call to confirm.


## 2026-09-01 — two Under Contract threads: a condo called two properties, and a built flyer withheld over twenty points

Reported by Chase from #calvo with both threads pasted. Four defects, all fixed
with regression tests; deployment and the two stuck runs are covered under
"What I need from Chase".

**Lina Mariner, 10600 Partridge Ln Apt B3.** The form gave `10600 partridge
lane b3`. Gable asked for the whole address and the photo, Carmen supplied both,
and Gable then asked "which one is this post for?" — three times, while she
said three ways that it was one condo. The house number is five digits, and the
two-property check counted it as a second ZIP. The same pattern had already
made the opening message say only "it has no state" when the address had
neither a state nor a ZIP. `listings.address.zip_codes` is now the one ZIP
reader and leaves the house number out; the manifest, the incomplete-address
sentence and the research identity window use it.

**Brittney Bushee, 2038 Kurtz Ave.** Three things, in sequence.

1. Gable built the flyer, then said the agent photo ran 20 points past the
   bottom edge and did not send the link. Measured through the service
   account: the design's own headshot well bled past the page, and the face was
   created inside it, clipped clear of the title band — 40 points lower and 20
   points shorter than the well — so the audit matched it to no frame and
   charged the design's overhang to Gable. A created image inside a frame Gable
   deleted now inherits that frame's overhang.
2. Carmen said "That's ok. Please send it and I can adjust" and was refused.
   The flyer existed; its link was in the database. A layout regression is now
   delivered with the measurement under the link, the way a vision finding has
   been since 2026-08-17. Chase's rule: Gable produces the flyer no matter what.
3. "Recheck it" rebuilt from the source Carmen had just edited, and the
   official-site profile read timed out once. The exception was swallowed with
   no log line, the memoised gate reused the failure for the credential phase,
   and the pause told her to correct the request or the roster — both right.
   The read is retried once after a transient failure and logs the cause; a
   silent site on a complete roster row now yields the brokerage credential and
   says so in the delivery message; a pause the silence still forces names the
   true remedy, which is to run again.

**Verified.** The whole suite, `mypy --strict` and `ruff` pass with the new
tests: the condo address, the clipped face inside a bleeding well, the
delivered layout note, the retry, and every silent-site branch.
`agents/profile_lookup.py` was split from `website.py` at the 800-line ceiling.

**Deployed (`1207109`) and the runs resumed from the droplet.** Brittney's
flyer is in her thread, built from the source Carmen edited at 20:01, with no
layout note and the credential read from her profile — the site answered this
time. Lina's first resume found a fifth defect: `tools/run_row.py --resume`
re-read the sheet's `10600 partridge lane b3` instead of the address Carmen
had stated in the thread, and posted one more address ask there. The Slack
path had always laid the stated address over the row; the tool did not.
`store.stated_address` is now the one reader both use, with a test. That
message in her thread is this session's, not Gable's design.

**Not a defect, but parked:** a Sold listing from 2026-08-31 evening is still
waiting for its property photo in its own thread.

## What I need from Chase

- **2026-09-21: deploy, then a "rerun" in each of the two #calvo threads.**
  Pushed and rehearsed; not deployed, because that restarts the listener
  Carmen is using. After deploying, both threads need a reply in their own
  thread to pick the fix up. I did not post to #calvo.
- **2026-09-21: one reversal to confirm, not blocking.** A website that answers
  and has no page for an agent now yields the brokerage credential when the
  roster row is complete — the same relief a silent website already got. It
  reverses one sentence in the 2026-09-01 decision row, and the new row says
  so. Emptying `GABLE_DEFAULT_AGENT_CREDENTIAL` still restores the old stop
  exactly. If you would rather a missing page keep stopping the run, that is a
  one-line revert and Melanie Kim's Open House stays unbuildable until somebody
  edits her profile on the brokerage site.
- **2026-09-21: Melanie Kim's website profile still says Humeniuk.** Nothing in
  Gable depends on it any more, but her public page and her open-houses page
  both carry the previous name, and somebody at the brokerage may want to fix
  that regardless.
- **One judgment call to confirm.** A silent website now yields the brokerage
  credential when the roster row is complete. Emptying
  `GABLE_DEFAULT_AGENT_CREDENTIAL` restores the old stop, as the 2026-08-19
  decision promised; nothing else changes.
- **If Lina's thread still shows no flyer**, the second resume did not land.
  Reply "run it again" in her thread, or from the droplet:

  ```
  ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
    "cd /opt/gable && sudo -u gable ./.venv/bin/python -m tools.run_row 'Form Responses 1' 136 --resume"
  ```

Older entries: `STATUS_ARCHIVE_2026-08-27.md`, then the archives before it.


## 2026-09-20 — three designs could not fill the agent's name, found while rehearsing

Carmen's 2026-08-26 edits changed the sample agent on New Listing and Open
House to **Lina Mariner** and on New Listing with Open House to **Brittany
Tawney**. `SAMPLE_AGENT_NAMES` matches the literal a design ships with, so none
of the three resolved `agent_name` any more, and the slot was simply not
replaced. A rehearsal flyer for Andy Jang came back carrying **Lina Mariner's
name above Andy's phone, email and face**.

Nothing wrong reached Carmen: the last delivered New Listing was 2026-08-19,
before the edit. The next one would have. The two names are now in the table and
a test reads the live New Listing text verbatim.

**Three things this turned up that are yours, not mine:**

1. **New Listing with Open House cannot fill its phone at all.** It carries
   `C: 717-524-8010` over `O: 410-305-9006` in one box. The `C:/O:` convention
   is supported, but those exact numbers are not in `SAMPLE_CONTACTS`, and
   `run_values.OFFICE_PHONE` is `443.499.3839` — a different office number.
   Whether 410-305-9006 is the current office number that should stay, or both
   should be replaced with the agent's, decides what gets printed on a real
   flyer, so I did not guess. Today the design refuses to deliver, which is the
   safe behaviour: the run stops at "the phone number 717-524-8010 is not this
   listing's". That is the one design of the three still blocked.
2. **The canary built New Listing the same day and reported nothing wrong** —
   while the flyer it built carried the wrong agent's name. It checks fields,
   frames, fitting and geometry, not "every slot the design displays was
   actually filled". Adding that check is the thing most likely to catch the
   next edit, and it is a decision about what counts as a clean canary.
3. **Nothing is wrong with the budget.** I reported the ceiling as exhausted;
   it is not. $59.49 reserved since 2026-08-11 against a configured $500, and
   the live service reads that setting correctly. What was capped at $50 was
   the rehearsal tool's own process, because it skipped `configure_ceiling`.
   No decision needed from you.


## 2026-09-20 — the thread audit has never read a thread

`tools/audit_threads.py` asked Slack for `oldest` with seven decimal places.
Slack timestamps have six, and `conversations.history` answers the long form
with an empty list, `ok: true`, and no error. Since it was written on
2026-09-01 it has printed **"0 thread(s), 0 flagged"** and exited 0 for every
channel and every window.

CLAUDE.md §7 makes this the exit condition for Phase 1 — "'Clean' is measured by
`tools/audit_threads.py` against #calvo, not by the runs table" — and the
standing instruction was to run it daily through the watch week. It would have
reported a clean week without reading anything.

Fixed and pushed. **The first real run, 30 days of #calvo: 52 threads, 44
flagged.** Most are a single announcement with no flyer link after it. Two are
long: Elliot Mitchell's Under Contract (5 messages) and John Murrow's New
Listing with Open House (6 — the three-photo refusal). That list is the real
state of the last month and wants reading before anything is concluded from it;
I have not classified them.

## 2026-09-20 — Carmen's three-photo thread cannot be finished yet

Chase asked for the flyer to be built into the thread where Carmen originally
sent the three photos (#calvo, `1789769011.017599`). I have not posted there.
Her run `run-d48d586200a7` is still parked in `needs_photo` — she said "can
cancel the request" and there is no cancel tool, so it never closed — and her
three uploads are still recoverable: `F0C2TTL0059`, `F0C3021QEEN`,
`F0C3UFCSXEC`.

**The credential overlap is fixed.** The first flyer built into her thread had
"TEAM LEADER + REALTOR" wrapped on top of John Murrow's phone number.
`measure.text_boxes` was reading grouped text as `declared * group_scale`,
which made a box that overflows look like it fits, so the fitter left it alone.
A group's scale shapes the box and does not scale the type — measured against
the renderer both ways. Text that does not fit is now always shrunk.

**The phone box is fixed.** It needed no decision after all: the shipped
`C:/O:` convention already replaces the whole box with the agent's own number
rather than printing any office, so the two current sample numbers just needed
recording. That design now builds — rehearsed in the playground with Carmen's
own three photos, on her design, and the flyer is right.

**One thing still stops her thread: John Murrow has no headshot on file.**
Gable said so in its first message there, and the Head Shots folder is
human-owned — Gable cannot fill it. Add a headshot for him and the run resumes
onto all three of her photos.
