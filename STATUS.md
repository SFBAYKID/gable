# Gable — status, and what's needed from Chase

Last updated 2026-08-27 by the building agent.

## 2026-08-27 — Porsher Howard's Client Review Post: asked five times for a photo the design cannot hold

Reported by Chase from the live channel while it was happening. Fixed; **not yet
deployed, and the stuck run is still parked** — see "What I need from Chase".

**What Carmen saw.** Gable opened the thread, asked for the review quote and
"separately, can you send me the property photo here?" She answered both: the
quote, and "there is no property photo needed for this request." Gable asked
again. She said "the image should be the headshot for Porsher. It's in the
headshot folder." Gable replied "Got it, Carmen. Use Porsher's filed headshot,
not a property photo" — and did nothing; the reply was `tool=none` and the run
stayed in `needs_photo`. Chase asked whether it could do this; Gable said yes,
then asked for the image twice more. Five asks, three people saying no.

**Why it could not be unblocked.** A missing photo is deliberately outside what
`build_with_blank_fields` waives, which is correct for a design that has a photo
well. This one does not, so there was no reachable answer.

**The measurement.** Read through the service account against all six live
designs. Every real property well is landscape and wide — 1.52 to 2.20 aspect,
67% to 100% of the slide. Client Review Post has exactly **one** image well:
5.55x9.49in, aspect **0.58**, portrait. That is a headshot by the band measured
on 2026-08-26, and the same 0.58 as Open House's real headshot. It was recorded
as the design's hero.

**The second half, which nobody saw because the photo never came.** Client
Review Post was the only one of the six where `headshot_frames` returned
nothing — the hero claim ate its one portrait well first. Had Carmen sent a
house, Gable would have deleted the agent's portrait and placed the building
there. A testimonial with no face on it, and no message saying so.

**Fixed.** `designs.NO_HERO_DESIGNS` records the measurement; `find_hero_frame`
refuses those designs (the geometric search would otherwise re-find the well);
the manifest drops `_HERO`; preflight stops calling a heroless design defective;
`runner` asks for a photo only when the manifest declares one. `place_headshot`
now receives the design label. `pipeline/run_images.py` split out of
`runner.py`. Regression tests at three levels, and the runner-level one
reproduces `needs_photo` exactly when reverted.

**Verified live against the real design:** no hero demanded, `p1_i90` now
resolves as the headshot well, and preflight comes back with zero blocking
issues once Carmen's quote is in hand.

**Worth noting for the next agent.** The ask and the build read different
sources for the same question — the build already guarded its hero work with
`if hero_slot:` while the ask was unconditional. When a check has an "ask" side
and a "build" side, make them read one source. This is the second time in eight
days that a repeat rather than an error was what Carmen saw.

## What I need from Chase

Nothing on this one. Deployed (`eda7fe6`, `75e8d94`), the run was resumed, and
Carmen has the flyer in her thread. The open questions closed themselves:

- **The client name.** The design resolves a `client_name` field whose sample
  reads "OLIVIA WILSON". Carmen signed her quote "-Sharon", Gable recorded it
  as `client_name`, and the flyer prints **Sharon**. Confirmed on the render.
- **The quote's punctuation.** Gable delivered the flyer and said the
  testimonial is missing a stop between "communication skills" and "She made".
  That is Carmen's supplied text, so it flagged it rather than editing it,
  which is the right call. She may want to fix it in the thread.

**Two smaller things fixed on the way out.**

- `tools/run_row.py --resume` refused this run because it sat in `needs_photo`
  — the state the manifest fix exists to stop producing. A run parked on a
  design with no photo well is waiting for something that can never arrive.
- The delivery message ended "send another photo here if you want it framed
  differently and I will redo it", on the listing where Gable had just asked
  five times for a photograph the design cannot hold. `run_reporting.reframe_offer`
  now offers it only where a photo well exists.

## 2026-08-26 — Brittany Tawney's Open House: forty minutes of work caused by a false positive

Reported by Chase from the live channel while it was happening. One submission
blocked, six templates rebuilt to satisfy a complaint that was never real, and
five separate defects behind it. All five fixed and deployed the same day.

**What Carmen saw.** At 11:54 her listing thread said the open-house tag on
`New Listing with Open House` was cut off at the right edge. It is not cut off;
it overhangs the page on purpose, which `d613511` established on 2026-08-14 and
which the flyer already delivered from that source shows. Gable had not looked
at 11:54 either — it replayed a verdict stored on 2026-08-19. She trashed the
design and spent the next forty minutes importing replacements, and Gable
answered with eight messages, one of which repeated itself four minutes later,
one of which sent her to convert a file it could never have selected, and one of
which told her to restore a file she had correctly deleted. Two direct asks to
check everything again were refused for want of a matching thread.

**The five defects.** Each has a row in `DECISIONS.md` and a regression test.

1. A verdict about how a design LOOKS blocked a new listing. `blocker_kind`
   (schema v15) now records why a design is refused, and only `visual` clears.
2. `_HEADSHOT_ASPECT` ran to 1.70, admitting landscape wells. On `Open House`
   this was not merely noisy: the true headshot well is 0.58, under the old
   lower bound, so the one surviving candidate was a property-photo well and
   Gable would have put an agent's face in it, silently.
3. An unchanged refusal re-posted on every Drive revision bump.
4. "Check them all again" had no whole-folder entry point.
5. Deleting a file Gable had asked to have replaced was treated as a mistake.

**Worth noting for the next agent.** Defect 1 is the one that cost the day, and
it was not a new mistake — it was a decision applied to one of the two callers
that read it. When a finding says "this check is wrong here", search for every
consumer of the stored result, not just the one in the traceback. Defect 2 was
found only because the advice Gable gave about defect 1's aftermath looked wrong
enough to measure; nothing in the suite would have caught it.

## 2026-08-21 — Deborah Manarin's Sold: asked three times for an address it had

Reported by Chase on 2026-08-24 from the live thread. One listing, three asks,
the same sentence each time — and the third one printed the state inside the
clause denying it: *"I have this listing at 2519 Ann Arbor Lane, Bowie,
Maryland 20716, but it has no state."* Two independent defects, both fixed,
both with a regression test. Deployed 2026-08-24 (24e5519). The run itself was
parked before the fix and could not restart itself, so it was resumed once by
hand with `tools/run_row.py --resume`; it reused Carmen's photo, built in the
same thread, printed `2519 Ann Arbor Lane, Bowie, MD 20716`, and Chase confirmed
the flyer. A submission arriving from here needs no nudge.

**1. A state written out was invisible.** The form said `2519 Ann Arbor Lane
Bowie Maryland 20716`. Every check downstream reads a state as a two-letter
postal code, so the address failed `manifest.ADDRESS_SHAPE`, and
`needs.incomplete_address` — testing the same set — reported the fault as "no
state". `address.tidy` now folds a written-out state into its code when it sits
where the state belongs, which is the only token it rewrites. Maryland Avenue,
Georgia Avenue and California, MD are all covered by tests, because every state
name is also a street or a town.

**2. The whole address she sent was dropped on the floor.** Carmen answered
"2519 Ann Arbor Lane / Bowie, MD 20716" with the photo attached. The caption was
read and the model extracted the address correctly; `runtime.record_photo_caption`
then called `answers.record_stated` without the `response_row_id`, and an address
is the one value that cannot be stored without it. The droplet log records it
exactly: `a stated address arrived with no submission to attach it to`. The
caption hook now takes the submission and the handoff reloads the row before
resuming, so a corrected address in a caption is what the flyer is built from.

`slackapp/uploads.py` — the Slack download boundary and nothing else — was split
out of `photos.py` in the same change to stay under the 800-line ceiling.

**Worth noting for the next agent:** neither defect was in the run state
machine. Both were a value crossing a boundary in a shape the far side could not
read, and both produced a *repeat* rather than an error, which is the failure
mode Carmen sees as Gable not listening. §2.5's list is worth extending with it.

## 2026-08-20 — Effie Fafaleos' Open House: a photo Gable asked for and refused

One listing, two defects, both fixed and deployed. Carmen sent the photograph
Gable had asked for and was told "This listing is not waiting for a photo, so I
left the current flyer unchanged." No flyer, and nothing in the thread said why.

**1. Gable asked for something it had no state to receive.** The run posted one
message asking Carmen to widen the Open House design *and* to send the property
photo, then parked in `needs_template`. Only `needs_photo` and `needs_review`
accepted uploads, so the answer to half the message was refused, and the only
exit was starting the row over. The photograph is now recorded on the run as its
own fact (`runs.awaiting_photo`, schema v14) rather than inferred from the
status, and any paused state that asked for one accepts it. A paused run that
asked for nothing still declines a stray upload. The same signal closed a second
hole found while fixing it: "build it with blanks" would release a run still
owed its photo and build a flyer with no property photograph on it.

This is the third instance of one class in two weeks — the credential remedy,
the address ask, and now this — so the regression test is written against
**every** paused state rather than the two that were reported, and a new paused
state inherits the invariant instead of quietly falling outside it.

**2. The blocker named a remedy that cannot work.** "The open house would need
about 227 percent more room... Widen that section." The request named three
open houses — `Friday, Aug. 21 4pm to 6pm, Sat. Aug. 22 10am to 12pm, Sun, Aug.
23 11am to 1pm` — and the design draws one date box and one time box. No width
holds three different hours in one time box.

Worse, the width check was the only thing standing between that request and a
bad flyer. The splitter's "two different times are two facts" rule was applied
to bare times only, so this value was carved into "4pm to 6pm" in the time box
beneath a date box still reading "Friday, Aug. 21, Sat. Aug. 22 10am to 12pm,
Sun, Aug. 23 11am to 1pm". A wider box would have shipped that.

Chase settled what happens instead, the same day: **Gable cannot get stuck — it
produces the flyer no matter what.** Asking is allowed; stopping is not. So the
first open house goes on the flyer, and the delivery message names what was
left off and offers to rebuild for another date. Carmen gets the link and the
choice in one message.

**3. A first fix broke the exit it was meant to open, and a review caught it.**
The `awaiting_photo` signal was wired into the "may I build without a photo"
gate, which also gates "may I re-measure the template". A `needs_template` run
owed a photo would then have refused *"tell me to check the updated template
again"* — the exact instruction its own blocker gives. Three more from the same
review: `resume_claim.PHOTO_RESUME_STATES` did not include `needs_template`, so
the real claim took an unguarded branch and lost the upload outright during the
acknowledgement gap; startup recovery released an abandoned photo claim in
silence for every paused state but `needs_photo`; and the CLI's
`--hero-photo-url` never cleared the flag. All four are fixed, and the test
double that hid the second one now routes through the real claim.

**4. The delivered flyer printed "6pm" across "3 BATHS".** Caught by looking at
the rendered result rather than the log. "4pm to 6pm" does not fit a 72pt time
box drawn for "2-4PM", so it wrapped to three lines and overflowed into the
stats row. Gable measured that and said so, then delivered — right for a flyer,
wrong for a value it could have written the way the design writes it. Times are
now compacted to the designs' own idiom: "4pm to 6pm" is "4-6PM".

**5. The review's remaining items are closed.** A photo the visual check
refused now invites its own replacement without needing an exact phrase (the
verdict for that was computed and read by nothing). The two-image ask names
both destinations out loud. And three smaller untruths went with them: a
"I left the current flyer unchanged" said where no flyer existed, a hero photo
described as attached when a check had refused it, and a run's state decided by
string-matching a user-visible sentence.

**Every item the review raised is now closed.** Sixteen defects in total,
across eleven commits, all deployed and verified live on the droplet. One
residual window is worth knowing about rather than acting on: `awaiting_photo`
is written just before the question it describes is persisted, so a crash
between the two leaves a run that ACCEPTS a photograph without having asked for
one. That is the safe direction — the alternative is a run that refuses one it
did ask for, which is the bug this all started with. Moving the flag onto the
`run_questions` row would close it entirely and is the right shape if this area
is touched again.

### Needed from Chase

- **Watch whether the two-image ask is now clear enough.** When a design has no
  headshot on file, Gable names two images at once. Both sentences now say
  where each goes — the folder, or this thread — which was the actual failure
  mode. It still takes any image in the thread as the property photo, so if
  Carmen sends a headshot there anyway it becomes the hero. That is visible the
  moment she opens the flyer and costs one cycle. If it happens, the harder
  guard is a question when both are outstanding, at the cost of a round trip on
  every run with a missing headshot.
- **This request also asks for a photo collage** — "please make a photo collage
  of the front of house, kitchen and office". Noted as over the top; Gable fits
  one hero photograph to one frame and says nothing about the collage.

## 2026-08-19 — Caleb Olawuyi's credential, and a loop Gable talked Carmen into

Carmen spent about five minutes of her morning being told the same impossible
thing four different ways, and Chase had to step in. Three defects, all fixed
and each verified; one decision is Chase's and is blocking one listing.

**What Gable got right.** Caleb's profile really does have an empty job title.
`cornerhouserealty.com/caleb-olawuyi/` serves his email and direct phone from
its contact block and an empty `cbl__widget--job_title` — read live. Under
Contract prints a credential, credentials may only come from that profile, so
the run correctly paused. Carmen read "no title or credential" as "his contact
details are missing", saw them on the page, and reported Gable as wrong about
something it was right about. The refusal was sound; the sentence was not.

**What Gable got wrong.**

1. **It named a fix that cannot exist.** The pause reused the generic contact
   remedy — "correct the request or Agents Contact Information". Neither can
   carry a credential: `validate_contact` reads the title only from the
   profile, and the roster workbook has no title column. Carmen obeyed it three
   times. The credential pause now says the contact details are fine, that the
   profile's job-title field is empty, that telling Gable cannot reach him, and
   that the profile is the only place it changes.
2. **Its own instruction broke its own lookup.** Told to add REALTOR to the
   request, Carmen wrote the name as "Caleb Olawuyi, Realtor" — and the next
   run refused with "no exact profile for this agent", which is worse than the
   refusal before it. The branded-name match tested a string prefix and read
   the comma as part of the surname. Names are now compared word by word;
   verified live that both spellings resolve to the same profile.
3. **It answered a message addressed to somebody else.** Gable replied "Take
   your time, Chase" to Chase's "@Carmen one sec let me look at this", and had
   already answered Carmen's "@Chase? I'm not sure what to do here". A reply
   naming a person other than Gable is now left alone.

### Settled the same day: one brokerage-wide credential

Chase confirmed all 38 roster agents are Realtors and asked for the code change
rather than a per-agent website edit. `GABLE_DEFAULT_AGENT_CREDENTIAL=REALTOR`
now fills a title the agent's own profile leaves blank. The per-person guess
stays forbidden, a profile that states a title still wins, provenance separates
`official_website` from `brokerage_default`, and emptying the variable restores
the old refusal exactly. See the reversing row in `DECISIONS.md`.

Caleb was also added to Agents Contact Information, and the workbook name check
now tolerates a request carrying branding — his request still reads "Caleb
Olawuyi, Realtor", and without that it would have failed on a name conflict the
moment he was filed. The filed name is what prints, so the credential Carmen
typed into the request never reaches the flyer.

**A third message defect, same shape as the first two.** Gable announced the
listing by its address and then said "I still need the address". The check is
right — no state, and the design prints street, city, state and ZIP — but the
sentence contradicted the message above it. It now echoes the address in hand
and names the fault. Chase spotted this one; that is three times in one morning
that a correct refusal was wearing a wrong sentence, which is worth treating as
a pattern rather than three bugs.

**Two more from the same afternoon, both fixed.** The credential default
shipped in capitals, and because the case rule only ever capitalises, REALTOR
went into a box drawn for "Realtor" and autofit shrank the line — Carmen saw
that as the spacing shifting. It is title case now. And an unprompted template
re-read that found nothing was announcing itself in #calvo; Carmen edited three
designs in four minutes and got three non-events. A clean automatic re-read is
silent now; every problem, warning, new design and explicit re-check still
speaks.

**The form's email is the submitter's, not the agent's.** Two requests stopped
minutes apart, both filed by one person for other agents. Gable identified the
agent by that address, so it proved nothing. It now falls back to the roster by
name — exactly one row, profile still corroborating — which resolves Tonette
Campbell outright, because her filed row and her profile already agreed on
everything. An agent the roster does not carry still stops, and now says so.
Mike Nugent is that case: no row exists for him despite Carmen believing she
added one.

**A flyer stated a fact nobody supplied.** Mike Nugent's read "3 Bathrooms"
because that is what New Listing is drawn with and Carmen had given only beds,
square footage and price. The rule that leaves a design's placeholder showing
was written for "PROPERTY ADDRESS", which reads as a gap; a bare number reads
as data. Auditing the 31 flyers delivered since going live found three: Andy
Jang's bedrooms, Lina Mariner's asking price, and Mike Nugent's bathrooms.
Those slots are now blanked when unfilled. **The two older flyers were
delivered to Carmen and may have gone further — they are worth checking with
her.**

**The photo bug is fixed at the root.** Gable was never subscribed to Slack's
`file_shared` event, so when Slack posted a message first and attached the file
a moment later, the upload was never announced to it at all — that is why
Caleb's photo vanished and Carmen's next one worked. The event was added to the
live app on 2026-08-19, and `shared_file_event` feeds it into the existing photo
handoff. Not yet proven on a real upload; the next photo Carmen sends is the
test.

**Gable now reads the counts agents write in their own details field.** Both
1921 Lincoln Ave requests said "3Bed/2 Bath" there while their flyers printed a
sample bathroom count — three on one, five on the other, for the same house.
The answer was in hand and unread. Chase asked for the cause fixed and the two
flyers left alone, so they still show the wrong figure and are not to be sent.

**Still worth undoing by hand:** the agent name on Caleb's request row, and the
Property Address cell on row 116, which now reads "Tonette Campbell" — her
flyer built before that edit and carries the right address, but a re-run would
not. Gable
reads that tab and never writes to it, and nothing now depends on it being
fixed — but it is still wrong in the source of record.

**Worth acting on next:** `runner.py` sat at exactly 800 lines, so one setting
broke the ceiling and `RunResult` was moved out to buy room. `_sequence` is a
single 489-line method; that is the real design signal, and splitting it is its
own task rather than something to wedge into a hotfix.

## 2026-08-17 — Reels and Stories are skipped, silently

Carmen confirmed to Chase that only the static posts are graphics; an Instagram
Reel or Story is video or animation her team makes by hand. The form has always
carried that answer in `Select social media content type`, and Gable has always
ignored it, so every Reel would have become a flyer. **40 of the 112 live rows
ask for a Reel or a Story** — better than a third of everything submitted.

The poller now checks the content type before opening a run, records the skip
terminally so it never reopens, and posts nothing about it. Silence is Chase's
call. An unknown or blank value still builds, because an extra design costs
Carmen a glance while a silent skip is a request that disappears with nobody
told; unknown values are logged so a new form option surfaces there.

Two things were found on the way and are worth knowing:

- **The live `Form Responses 1` has changed shape.** It now splits the agent's
  name into `First Name of Agent` / `Last Name of Agent` and has dropped its
  trailing `Notes` column, so every column from D rightward moved one right.
  Nothing broke, because columns are found by header text — but the positional
  fallback in `intake.COLUMNS` now describes a tab that does not exist, and the
  `LIVE_HEADER` fixture in `tests/test_intake_headers.py` is a transcription of
  the older shape. Both are labelled as such rather than silently trusted.
- **`content_type` did not survive a database round-trip** until schema v13
  added it. A submission reloaded for a resumed thread would have read as
  "build" regardless of what the form said.

### Known dead end — Chase's call, not mine

**A Reel corrected in place to a static post is never built and never
mentioned.** Verified by running it: the poller skips the Reel terminally, the
agent edits that same form row to `Static Instagram/Facebook Post`,
`record_submission` correctly stores the new value — and nothing happens.
`response_row_id` is `source_identity(tab, sheet_row)`, which is stable across
an in-place edit, so `has_been_handled` still sees the terminal `skipped` run
and `new_submissions` filters the row out before the gate ever looks at it.

The run's stated reason ("video or animation") is no longer true of the stored
row, and unlike a delivered listing there is no Slack thread in which to ask for
a rebuild. The workaround is to submit a new form response, which lands on a new
sheet row, earns a new identity, and builds normally.

I did not fix it. The fix means reopening a row `has_been_handled` has already
retired, and that guard is the thing standing between Gable and 99 unwanted
flyers — CLAUDE.md §2.6 puts a change like that with Chase. The narrow version
would reconsider only a submission whose single run is a content-type skip and
whose stored content type has since become a graphic. Worth doing if agents turn
out to mis-select the content type; not worth touching the guard speculatively.

### Owed, not blocking

`src/gable/listings/intake.py` is 750 lines. That is under the 800 hard ceiling
and over the 300–500 target, and it was already over before this change. It
wants a split — the column mapping, the content-type gate and the
coherence/completeness rules are three separate concerns — but doing it inside a
scope change would make this diff unreviewable.

## 2026-08-16 — Gable is live in #calvo with Carmen

`GABLE_SLACK_CHANNEL_ID` now points at **`C0BP597644B` (#calvo)**, the production
channel, at Chase's instruction. The previous value is preserved in a timestamped
`.env.bak-*` on the droplet. Testing that is not meant for Carmen must move back
to `C0B02721MNK` (monarch-bot-playground) first — CLAUDE.md §11 still governs.

Gable posted its own introduction there, and the service is watching the sheet on
the two-minute business-hours interval. The next real form submission is the first
live listing.

One defect was found and fixed on the way: `voice.shorten` flattened every message
over 600 characters into a single block and dropped its tail, because the sentence
split ran across the whole message on a whitespace run. It was caught by running
the introduction through Gable's own style gate before posting it. `mypy --strict`
was also failing on three implicit re-exports left by the `preflight` split; both
are fixed, and all 45 previously unpushed commits are now on `origin/main`.

### Still waiting on Chase

- Kelsey Mahon and Lina Mariner have no headshot in the roster.
- Piet de Dreu's headshot is not a cut-out, so it renders as a rectangle.
- Around six agents fail the official-profile identity check.
- A stray test photo and a stray text message sit in #monarch-bot-playground.

## 2026-08-16 rounds 8 and 9 — round 9 is the clean one

Round 8 found one defect: row 16's open house reads "7/11/2026", a date with no
time, and Open House sets the two in separate boxes. The date filled, the time
box was blanked, and the flyer showed the design's own two separators with a gap
between them. Fixed at the root — preflight now reports any box this run would
BLANK so the missing part joins the one batched ask, before the copy is made.

**Round 9 then ran all six designs and found zero code defects.** Sold, Under
Contract, Open House, New Listing, New Listing with Open House and Client Review
all delivered, all with zero layout regressions against their source designs,
with no pending notifications, no open photo ingress claims, and no errors in
the log.

Everything the visual gate stopped in round 9 was a photograph I had cropped
badly — a low-angle shot that was mostly sky, a cabin cropped so the house was
gone. Each was a correct refusal of a genuinely bad hero image, and each was
resolved by supplying a photograph matched to the frame's measured aspect. That
is the gate doing its job, not a defect.

Two designs had no unused form row left (New Listing with Open House, and every
Client Review row is at the three-attempt ceiling), so those ran through the
post-delivery replacement path instead: "replace the property photo with this
one and run it again", then the photo. Same pipeline, same checks.

## 2026-08-16 round 7: two more real defects, both caught by the visual gate

Every design run again with new agents and new photographs. Two defects, both
found by the rendered inspection and neither reachable by the deterministic
checks — which is the argument for keeping that gate:

- **A note of pure punctuation was printed.** Row 110's details column holds a
  single "?". The dismissal list stripped " .!" and not "?", so Douglas White's
  Under Contract flyer showed a callout panel containing nothing but a question
  mark where the design says "Ready to Buy? / DM me to find your next home." A
  note now needs one letter or digit to be printed at all.
- **A value was shrunk to a caption between two headlines.** Louis Smith's Open
  House fitted "2:00 to 4:00 p.m." to 9.0pt in a row whose date and price
  stayed at 24.2pt. It cleared the 8-point absolute floor, so it was applied.
  The floor is now also relative to the siblings the designer sized with it,
  and only to those — a box sized for the word "Email" has no peers left
  standing and is still allowed to shrink hard.

All six then delivered with zero layout regressions.

**Testing note for whoever drives Slack next.** Every stray message in
#monarch-bot-playground this session came from one mistake: targeting the
composer by screen position instead of by containment. The rule that works is
to resolve the file input, the editor and the send button from inside
`[data-qa="threads_flexpane"]`, and to screenshot before sending. Clicks and
keystrokes both stop being delivered after a while; synthetic pointer events on
the pane's own send button keep working.

## 2026-08-16 two clean start-to-finish rounds, and the bugs they flushed out

Rounds 5 and 6: every design run end to end in Slack — announcement, one
batched ask, the human's reply with a fresh internet photograph, delivered
link. Round 5 surfaced one real defect (below), which was fixed and verified
live in the same thread; **round 6 then delivered all six designs with zero
new defects and zero layout regressions.** The rebuilt Open House also proved
the post-delivery value path: values sent after delivery reopen the run and
the rebuilt flyer carries them.

Fixed since the last entry, each with a decision-log row:

- A Slack message whose line break came back as a space could never be
  confirmed, so its outbox row logged an error a minute for a day.
- A run outcome persisted without a thread tried to become a new channel
  root and wedged; it now posts under its run's own announcement.
- Gable named only the first problem a checked flyer had; it now says all of
  them in one message.
- A flyer parked in review refused the replacement photo that was its only
  remedy.
- "11-1" with no am/pm never reached the time box; a footer complaint about
  the designer's own artwork parked a correct flyer (the deterministic
  geometric audit now outranks disproven layout opinions).
- The delivery message's offer — "send them here and I will run it again" —
  was refused by the post-delivery edit pause when taken; listing values now
  route to the rebuild path whatever the flyer shows.
- Roof-lines were cut on tall photos; words sent beside an unusable photo were
  discarded; a rebuild could race an in-flight upload and drop it. All three
  closed, with tests.

### Roster and form data gaps — Chase's calls, not code

These stopped runs correctly and cannot be fixed from the repo:

- **Kelsey Mahon and Lina Mariner have no headshot** in Head Shots; every
  design with a face slot refuses their rows.
- **Piet de Dreu's headshot is the only one of 41 that is not a cut-out**; it
  will render as a white box.
- Identity guards refuse: "Bobby Carr The Dog Walking Realtor" (row 42 and
  friends), "Kim Hixson® Infinity Home Team" (row 39), Tracy Edwards (workbook
  phone disagrees with the official site), Erica Pfeiffer (row 57 submitted
  email not on the official profile), Herb Bryant's row 104 (submitted email
  unknown), Lolo Simmons (no official profile page).
- Rows 5 and 50 (Client Review) are at the three-attempt ceiling from
  repeated test runs and need `tools/adopt_rows.py`-style clearance if they
  should run again.
- Row 85 (Jason Vetter) requests two agent placements — the dual-agent
  designs are Phase 2 by decision.

## 2026-08-15 confirmation pass, and Gable stops writing in blocks

All six designs rebuilt once more from their own threads, one message each —
"Rebuild this one." — and all six delivered. `slides/layout.py` reports zero
regressions against the designs they were copied from.

Chase on the writing: "She needs to say a message and have a \n so it's not
just one run-on sentence. His writing needs to be not one big block." Every
outcome and every refusal now separates its thoughts with a blank line. A
delivered flyer reads as the link, then what was done to the photo, then what
was fitted — three paragraphs rather than one paragraph of three sentences.

One defect found by the pass: dropping a note that only repeated the request
type left Under Contract's panel empty, and the missing-value check then stopped
the rebuild to ask what the note should say. An empty note panel keeps the
design's own "Ready to Buy? / DM me to find your next home."

**File size is no longer a proxy for a complete flyer.** Under Contract came
back at 0.31 MB against a 3.57 MB template, which looks alarming and is not: the
copy has 18 elements to the design's 18, the only absent source objects are the
photo well and the headshot frame Gable deletes and replaces, and the render is
complete. The earlier 3.57 MB copies still carried the template's own unused
sample photograph; these do not. Judge a flyer by its render and the layout
audit, not by its byte count.

Older entries are archived: 2026-08-13 and 2026-08-14 in
`STATUS_ARCHIVE_2026-08-14.md`, 2026-08-12 and before in
`STATUS_ARCHIVE_2026-08-12.md`.
