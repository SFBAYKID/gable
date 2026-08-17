# Gable — status, and what's needed from Chase

Last updated 2026-08-17 by the building agent.

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

## 2026-08-14 second pass: six new agents, six new photographs

Every design run again with a different agent and a different photograph, after
Chase's review of the first pass. The complaint that mattered was not a design
one: "some threads had 4 replies, some had 19 ... If a user has to go back and
forth 19 times they are just going to build it themselves."

| Design | Row | Agent | Size |
|---|---|---|---|
| Under Contract | 25 | Donald Clark | 3.57 MB |
| Open House | 16 | Sydney Kinney | 3.45 MB |
| Sold | 10 | Kim Hixson | 1.21 MB |
| New Listing with Open House | 78 | Tonette Campbell | 2.06 MB |
| New Listing | 29 | Kelli Kulnich | 3.45 MB |
| Client Review Post | 50 | Ian DePinto | 0.54 MB |

`slides/layout.py` reports zero regressions across all six, measured against the
design each was copied from.

**Round trips.** An unreadable address and an address the design cannot print
both now ride the one batched ask with the photograph instead of costing their
own turn. Sydney Kinney's flyer is what that looks like when nothing else is
wrong: an announcement, one ask, one reply carrying the photo and four values,
and the link.

**Two new instruments.** `slides/layout.py` measures a built flyer against its
own design and reports only what moved — all three defects Chase found were
rectangles the rendered vision pass had not mentioned. And `fitting` now counts
the lines text really wraps into rather than dividing total width by line count,
which is what put the ZIP of a long address on top of the panel below it.

**Still Carmen's, not Gable's**: Piet de Dreu's headshot is the only one of 41
that is not a cut-out, and every client review on the form is longer than the
panel it has to fit.

**Left in the playground**: an unsent draft sits in the channel composer reading
"3 beds, 2 baths, 1,528 sq ft, $230,000.The price is $325,000." It is a stray
from driving Slack through the browser, it was never sent, and clearing it is
one keystroke.

## 2026-08-14 every design run once, and polling is on

All six designs were run end to end against real `Form Responses 1` rows, each
in its own Slack thread with the photograph uploaded as a reply. Every output
matches its source design's file size exactly, so nothing is lost in the copy —
Under Contract is 3.57 MB.

| Design | Row | Agent | Size |
|---|---|---|---|
| Under Contract | 79 | Sara Wolz | 3.57 MB |
| Open House | 81 | Tambria Eaton | 3.45 MB |
| Sold | 90 | Deborah Manarin | 1.21 MB |
| New Listing with Open House | 98 | Kirby-Jay John | 2.06 MB |
| New Listing | 92 | Piet de Dreu | 3.45 MB |
| Client Review Post | 5 | Gina Moore | 0.54 MB |

Twelve defects were found and fixed on the way, each with its own row in
`DECISIONS.md`. The ones that would have stopped live work: an answer to Gable's
own question about the address was unrecordable, so the run sat forever; a job
title too long for its slot was a hard stop no one in Slack could clear; a
client review was asked for a property address it does not have; and a cut-out
portrait was cover-cropped into a square well, taking the top off Kirby-Jay
John's head.

**Polling is enabled.** `GABLE_POLL_ENABLED=true`, watching every 2 minutes in
business hours. Before the switch: the twelve unhandled historical rows — 12,
13, 46, 64, 66, 71, 72, 104, 106, 107, 109 and 110 — were adopted by exact
row/hash assertion, `tools.preview_poll --expect-none` returned zero, and the
first enabled scan baselined all six designs silently. No flyer and no Slack
message came out of any of it. The next live submission is row 111.

Two things are Carmen's rather than Gable's:

- **Piet de Dreu's headshot is the only one of 41 that is not a cut-out.** It is
  a photograph matted onto an opaque white rectangle with a transparent strip
  across the top, which is why his flyer shows a white block behind him. Every
  other portrait in Head Shots has proper alpha.
- **Every client review on the form is longer than its panel.** They run 400 to
  1,000 characters against a quote box drawn for about 280. Gable now accepts a
  shorter pull-quote as a reply and sets that; it will not trim a client's words
  itself, and that stays a person's decision.

## 2026-08-14 every design driven end to end, four of six deliver

Chase asked for one systematic campaign: every design against every
salesperson, one batched question, then the link, fixing anything that looks
wrong on the spot. Andy Jang was taken through all six designs overnight.

**Four deliver a finished flyer from one round of questions:** Under Contract,
New Listing, New Listing with Open House, Client Review Post. Each was verified
by looking at the rendered flyer beside its own source design, which is now the
standing check — Chase's instruction: always compare to the source design.

**Two are blocked by defects in the artwork itself**, not by Gable, and cannot
be fixed from a Slack thread:

* **Sold** — "Thinking of Selling? / Reach Out / Today." overflows its navy
  callout and "Today." lands on the footer bar.
* **Open House** — the footer slogan "Local expertise. Exceptional results."
  wraps below the page and is cut off.

Both are present in the source designs; verified by rendering them. Gable's own
work on both flyers is correct and the visual gate is right to refuse. **Chase's
call:** fix the two designs, or decide that Gable may deliver when the only
visual problem also exists in the source. The gate was deliberately not
weakened without that decision.

### What the campaign found in Gable, all fixed and each verified by a rerun

Twelve defects, listed with their evidence in `DECISIONS.md`. The largest class
was photo placement: **three of the six designs were replacing the wrong shape**,
so a flyer showed the template's own house instead of the supplied one, or two
houses stacked, or the Corner House logo sliced in half. Each was settled by
copying the design and deleting one shape at a time rather than by reasoning
about the geometry.

The rest: an ask that crashed a resumed run, a flyer built with no photo when a
design needs two layers removed, a correct flyer refused over a capitalisation
mismatch in its own read-back, sample data from three designs that would have
printed a real agent's name and a previous listing's open house on somebody
else's flyer, and two designs refused outright over measurement gaps that are
now closed.

### What is needed from Chase

1. The two design defects above.
2. **Kelsey Mahon has no headshot** on file, under either of her addresses.
   36 of 38 agents match; she is the only gap.
3. **Price Reduction has no design** in Generic Templates, so those requests
   stop. 24 of them exist in the submission history.
4. Five Head Shots files match nobody on the roster: Andrew Hixson, Brittany
   Litel, Chris Meldrom, Chris Yankosky, Douglas White.

The remaining 37 salespeople have not been run yet. The design dimension is
where every defect so far has lived; the rep dimension is data.

## 2026-08-13 a clean single-pass run, confirmed by Chase

`Testing_1` row 34 — Ian DePinto, Sold, 2808 Berwick Ave, Baltimore MD 21234 —
ran end to end with **no intervention**: three Gable messages, one upload, one
link, 25 seconds from upload to delivery. Chase confirmed the flyer. Deployed
`309b083`, polling off.

    18:58:22  New Sold request from Ian DePinto - 2808 Berwick Ave...
    18:58:22  Can you send me the image?
    18:58:36  Chase uploads images-1.jpg
    18:59:01  Your flyer is ready. <link>

Verified live: `delivered`, attempts 1, `ai_enhanced` false, **one** vision call
($0.10, down from $0.30 when it needed three), zero image-model spend, one run
for the response, zero pending notifications, no abandoned ingress. The render
was exported and inspected: roofline intact, photo filling the hero block, Ian's
cut-out headshot, correct address and contact details.

The earlier Louis Smith run was **not** a pass and was wrongly reported as one.
It reached a link only because a developer patched code and resumed twice; its
thread held two failure messages. A run that needs a terminal is a run that
would have stopped dead for Carmen. Judge a live test by the Slack thread, not
by the `runs` table.

## 2026-08-13 three defects the live tests found

Each needed a real photo in a real thread; no unit test would have found them.

1. **The visual inspection never ran.** Reasoning tokens come out of the same
   `max_output_tokens` as the answer; at `effort: high` the model spent 886 of
   1000 thinking and the JSON verdict truncated mid-array. It failed closed, as
   designed, but reported only "the visual inspection could not run". The
   ceiling is now 4000 and an exhausted budget is named in the log.
2. **The agent cut-out was matted onto white.** The portrait was fitted through
   the JPEG property path, so the transparent cut-out became an opaque white
   rectangle whose corner covered the address box. Two independent vision calls
   caught it. Portraits now fit as PNG with alpha intact; the hero photo still
   mattes, which is correct for a full-bleed image.
3. **The hero crop sliced the roof off.** A 3:2 photo into the 2.14:1 hero
   discards 30-38% of its height; centring took half of that off the top and cut
   both gable peaks while leaving an empty lawn below. 20% of the loss now comes
   from the top and the rest from the bottom.

The vision gate earned its place: it refused two flyers that were otherwise
complete, and it was right every time.

A fourth finding was not a bug. A 266x189 upload cannot fill a 1078x504 frame
and Gable correctly refused to invent 94% of a real house's pixels. Anything
640px or wider fills it. The fix for a small photo is a bigger photo, not a
generator.

`ARCHITECTURE.md` reached the 800-line ceiling, so the append-only decision log
moved to `DECISIONS.md`.

## 2026-08-13 the release candidate's gate is green

`ruff format`, `ruff check`, `mypy --strict` and 1,269 tests all pass locally.
Three failures the previous handoff recorded are fixed: a stale photo question
could be posted while the answering upload was still downloading, a test still
imported two names from `slackapp.runtime` after they moved to
`slackapp.source_refresh`, and two files were unformatted.

The photo race is closed properly rather than papered over. The handoff writes a
durable `file_share` claim before refreshing sources, downloading and
publishing, and releases it only once an outcome is stored; retiring the
question early is safe only because of that claim. `slackapp/recovery.py` reads
incomplete claims on every notification pass and either releases one whose run
already recorded the upload or asks for the image once more. Two mutation checks
confirmed the new tests fail when either half is removed.

`slackapp/runtime.py` was at the 800-line ceiling, so the startup-recovery
helpers moved into `slackapp/recovery.py` (runtime is now 700). The photo test
fixtures moved into `tests/photo_support.py` for the same reason.

**Nothing is deployed and polling is still off.** Blockers 2, 3 and 4 below are
open; none of them fire during the Mike photo-upload resume, which is why that
acceptance test can proceed first, but each must be closed or explicitly gated
before the behaviour it guards is switched on.

**`ARCHITECTURE.md` is now exactly 800 lines — the hard ceiling.** The next
decision-log row requires splitting it first.

## 2026-08-13 release audit after the pixelated Mike run

The current release candidate is not deployed. The playground service remains
on `c4c6f16`, active with `GABLE_POLL_ENABLED=false`; its database is schema 7.
Mike's existing run `run-e86567a4fa95` remains `needs_photo` in its original
thread, waiting for one correct replacement property image. The rejected draft
is retained for audit, but its link is not a delivery.

The release candidate makes every question, final link, review explanation,
failure, and interruption notice a durable Slack outbox item. Its retry loop is
independent of Sheet polling, and an answering owned-thread upload satisfies a
pending photo request before file preparation begins. A lost acknowledgement is
confirmed only by one exact Gable-authored match in bounded Slack history. It
handles small uploads with source-only fitting and fixes form identity for
duplicate timestamps and movable rows through whole-tab reconciliation and a
source-row ledger.
Headshot discovery now recognises both an empty Slides shape and an existing
portrait image only when that image shares a measured card with a resolved agent
name and phone, email or title. Logos, QR codes and secondary photos do not
qualify on shape alone; multiple candidates stop. A read-only walk of all 74
pages in the static source found three image objects, all icon-sized and none
misclassified. The live Sold source still has exactly one portrait shape,
`p1_i20`, and no competing candidate; replacement restores its layer boundary.

Automatic polling is not ready to enable blindly. A read-only preview against a
fresh dump of the live database reads all 105 production responses and finds
seven unhandled historical rows. Four are distinct answers that the deployed
timestamp/email/address key had collapsed into the preceding row: 13
(`3a86888ecab252cf`), 72 (`bbf229dd34a9154b`), 94
(`040dde7acebf55fd`) and 103 (`db31f2914f8fbbfe`). The other three are row 46
(`1d63ec043ba9ccdf`), row 105 (`a233bc6d3ad3ed38`) and row 106
(`f78a5d67b4cda3cc`). A temporary clone proved this set is deterministic, exact
adoption marks all seven skipped without repointing any existing run, and two
subsequent previews both return zero; production was not changed. Chase must
confirm they are historical. After deploy, `tools.adopt_rows` can assert the
seven exact row/hash pairs without Slack or flyer work, and
`tools.preview_poll --expect-none` must return zero before polling is enabled.
The live template catalogue is also empty because scanning has never run; the
first enabled scan safely baselines current files before the same poll can start
listing work.

## 2026-08-13: natural-language Slack and current release gate

The current review is in `AUDIT_2026-08-13.md`; the exact verification sequence
is in `TESTING.md`. The `/gable` declaration, `commands` OAuth scope, Bolt
handler, command service, operator queue, poll pause/resume controls, and their
command-only database queries are removed. The saved Slack app manifest was
updated successfully and its Slash Commands page now has no configured command.

Owned-thread natural language remains. Chase's exact sentence, “Hey, can you
rerun this project?”, deterministically reloads the current source and resumes a
paused run. The full gate caught and fixed the greeting-normalization mismatch.
The review also fixed a fail-open edge case where a confident negative visual
verdict with no explanatory items could otherwise leave the problem list empty
and deliver.

Commit `c4c6f16` is deployed in monarch-bot-playground and the service is active.
The controlled release target remains Testing_1 line 48: Mike Kulnich, Sold,
703 Perception Way, Aberdeen, MD 21001. The same owned-thread run retains Mike's
exact contact record, filed headshot, current Sold source, and supplied photo.

Property-photo fitting is now entirely local and deterministic. No image
generator or enhancement provider is connected. A source needing more than 2x
enlargement keeps its complete foreground at no more than 2x over a blurred,
darkened fill derived only from the same upload. Rejected draft links stay out
of Slack, and a confidently proved source-photo contradiction returns the same
run to `needs_photo` for one replacement upload.

The current repository also composites transparent uploads onto a deterministic
white matte, serializes memory-heavy image work, downsamples large headshots
before fitting, and rejects unsafe target dimensions before decoding. The
275×183 Mike upload can be fitted honestly, but its visible source detail cannot
be recreated. The release gate is therefore not successful until a supplied
photo produces a delivered flyer that Chase opens and confirms visually. Prior
provider experiments and credential exposure remain in the chronological record
below; they are not descriptions of the current fitting path.

## 2026-08-12 current quality gate

The six-part quality and triage audit is complete; current findings are in
`AUDIT_2026-08-12.md`. Source geometry preflight runs before any copy, new and
revised templates receive deterministic and placeholder-aware visual triage,
and an update in the same Slack thread reloads current Drive bytes under the
native waiting state. Listing clearance is revision-specific, template notices
survive Slack delivery failure without another paid inspection, and deleting a
source revokes its old ready state.

Slack photos retain their composition until the exact hero frame is measured,
hero and headshot replacements preserve the source frame's layer boundary, and
the final `gpt-5.6-sol` inspection compares the human photo with Google's
render. Conversation tool routing also uses Sol through the Responses API; a
live test caught and fixed the former Chat Completions failure, then proved that
“Update the image” produces a hero-or-headshot clarification.

The recoverable Drive smoke test copied `Sold`, detected roughly 34 address
characters of capacity against the 52-character certification target, recorded
`needs_template`, and verified the temporary copy in trash. No response-Sheet
write, Slack post, deployment, or finished-design publication occurred.

At that checkpoint, all 907 tests, Ruff format and lint, strict Mypy, Vulture,
dependency integrity, and diff checks passed. The Slack manifest still required
reinstallation then; the current manifest state is recorded in the audit above.
The OpenAI credential exposed during diagnostics must be rotated first; it is
not recorded in this repository or repeated here.

The sections below are chronological evidence and may describe an older state;
the dated audit and `ARCHITECTURE.md` are authoritative for current behavior.

**Handing off?** Read `TEMPLATE_CERTIFICATION.md` for what is actually proven
and which numbers can be trusted, `TEMPLATE_ISSUES.md` for the seven defects
that belong to Carmen, and `BRAND.md` for the fonts, colours and the fact that
not every design in the deck is a listing flyer.

## 2026-08-12 evening: delivered, from a sheet row to a finished flyer

Row 62 of `Form Responses 1` — Andy Jang, Sold, 300 Commerce St # D, Havre de
Grace — went from a simulated submission to a **delivered** flyer in Slack, with
no stop for review: the listing announced in the channel, the photo requested in
its thread, Chase's upload fitted and published, the `Sold` design taken from
Generic Templates by name, Andy's own headshot published from the Head Shots
folder, his direct line read from the contact workbook, and one closing message
carrying the link.

Five defects were found and fixed by watching that run rather than by reasoning
about it:

1. **The photo was pillarboxed.** `createImage` fits rather than fills, and the
   upload was cropped to the slide's 4:5 canvas instead of the frame's 2.14:1
   band — a narrow column of photograph with the layout showing either side.
   This was true of every design whose photo area is not 4:5.
2. **A question nobody was waiting for.** A non-blocking advisory was posted
   mid-build, phrased as a question, and then walked past. Advisories are now
   statements folded into the one closing message.
3. **Four messages for one flyer.** Now one, carrying the link.
4. **The indicator kept pulsing under a question**, which reads as "still
   working" and stops anyone from answering.
5. **A combined announcement started no thread**, so an upload had nowhere valid
   to land — the handoff matches a photo to a run by its thread.

**The `Sold` design has no price field**, so a sale price cannot appear on it as
built. If Carmen wants sold prices shown, that template needs one added.

## 2026-08-12: a full run, end to end, and the four things it caught

`Testing_1` row 78 (Eric Jacobs, Sold, 23 Pierside Ave Unit 118, $330,000) was
run from the sheet to a finished flyer in the playground channel: Gable asked
for the photo by name and request type, Chase dropped one in the thread, and it
was enlarged, fitted, placed, and rendered onto a Just Sold design with Eric's
own headshot, phone and email. It stopped at `needs_review` with the link and a
specific note, which is the design working.

Getting there exposed four defects, all now fixed and deployed:

1. **Columns were read by position.** `Testing_1` splits the agent name across
   two columns, shifting everything from D rightward, so its row 78 read as the
   acknowledgment paragraph for a request type and "Instagram Story" for an
   address. Columns are found by header text now, and an unrecognisable tab is
   refused rather than guessed at.
2. **The roster sync had been storing nobody, silently, all day.** The
   `Sales_People` header moved to row 1 when the tab was rebuilt with 39 agents;
   the range still started at A2, so Andy Jang's details became the column names
   and every row was skipped for having no email. Any flyer built today carried
   the brokerage's main number and the design's own stock face.
3. **The template picker only ever looked for its top choice.** Just Sold has
   eleven eligible designs and reported having none, because the one ranked
   first is not imported. It now takes the best design that exists, unless the
   submission named the missing one by cue.
4. **Two address normalisers disagreed.** The flyer's check ran its own weaker
   copy that only recognised an upper-case state, so "Baltimore Md 21230" never
   gained its comma and a complete run stopped to ask for an address already
   supplied. There is one set of address rules now, and a unit stays with its
   street so condo addresses still name their city.

**The open item is data, not code: only 11 of the 45 designs are marked
`gable_role=template` in the drive.** Just Sold has exactly one, so the design
choice was forced rather than made. Importing the rest is what turns selection
from theoretical into real.

`tools/run_row.py` starts one row by tab and number, and `--resume` continues a
paused source, information, or review run using its retained supplied photo. It
refuses `needs_photo`; that state clears only when Carmen or Chase uploads the
replacement in the owned Slack thread. The poller only ever starts rows it has
never seen, so a row already on the sheet cannot be run by waiting.

## Superseded: the 2026-08-11 progress snapshot

Removed 2026-08-16. It described a build with two verified flyers and no
headshot replacement, which every entry above contradicts. The findings it
carried that still matter — Spike A, the photo-shape proof, the certification
ledger — are in the sections below and in `DECISIONS.md`.

## 1. The two findings that reshaped the build

Removed 2026-08-16, because both are recorded where they are actually read.

**Spike A** — an uploaded Canva file types every column as text, so the photo
could never travel in it — is in `CLAUDE.md` §4.1, `DECISIONS.md`, and in full in
`spikes/SPIKE_A_RESULT.md`.

**The live form is not the form the spec described.** The column list kept here
had already been corrected twice and was stale a third time; the current reading
is `ARCHITECTURE.md` §3.1, and the code reads every tab by header text rather
than by position, so that section is the one that matters.

---

## 2. Decisions needed (blocking, ranked)

**D1 — RESOLVED. Neither (a), (b) nor (c): Gable renders in Google Slides.**

Canva was left behind entirely rather than worked around. The Slides API places
both the text *and* the photo — `replaceAllShapesWithImage` swaps a
`{{hero_photo}}` shape for an image fetched from a public HTTPS URL — which is
the exact capability Spike A proved an uploaded Canva file could never carry.
It needs no Enterprise plan, no marketplace review, and no TypeScript, and it
reuses the Google service account already required for the Sheet.

The pure Slides request builders and their concrete `pipeline/live.py` client
are built and tested. The former `src/gable/slides/renderer.py` and
`src/gable/canva/` paths were deleted. The options below are kept only as the
record of what was rejected:

- ~~(a) Text-only Bulk Create~~ — saves the typing, not the photo hunting, and
  the hunting is the twenty minutes.
- ~~(b) Phase 2 data-connector app~~ — gated on §4.3 item 4, and it is TypeScript.
- ~~(c) Canva Enterprise + autofill~~ — real money, quote-only.

**Resolved from this:** concrete Slides I/O is implemented in
`pipeline/live.py`, and the shared drive contains the 45 imported templates.

**D2 — RESOLVED, then replaced on 2026-08-12.** Template choice is a naming
rule, not a selector: one folder, and the file is named exactly what the form
calls the request type. The scored catalogue and its notes-reading ranking are
superseded — see the decision log. Their 45-entry inventory was removed with
the other unreachable selection modules and no longer exists in runtime code.

**D3 — RESOLVED.** Derived state lives in SQLite. Gable reads form responses,
mirrors the salesperson roster, and never modifies the response tab.

## 3. Questions that shape the build

**Q1 — RESOLVED.** The form branches across request types and the eleven
relevant columns are explicitly mapped in `listings/intake.py`.

**Q2 — RESOLVED.** Selected-source public property gaps are researched only
when a current result proves the exact street, city, ZIP, and source URL around
the extracted values. Legacy cached facts predate that proof and remain audit
records rather than build authority. A Sold closing price and Price Reduction
new price come only from their respective form columns; missing, unknowable, or
contradictory values pause rather than borrowing a public list price.

**Q3 — RESOLVED.** SQLite run state, not Sheet formatting, is the idempotency
authority. Historical rows must be adopted before polling can start.

## 4. Credentials — Chase only

Never entered by the agent (CLAUDE.md §3). It drives already-authenticated
browser sessions and consumes tokens from `.env`; it never types a secret.

Verified live by `.venv/bin/python tools/check_connections.py` on 2026-08-10.
That script makes a real call per credential and prints identity only, never a
value — run it after any `.env` change.

| Needed | For | Status |
|---|---|---|
| Slack app from `slack/manifest.json` → bot + app tokens | Any Slack output | **Done** — `auth.test` ok (team Monarch, bot `@gable`); Socket Mode ticket issued |
| Firecrawl API key | Agent verification | **Done** — key valid, 2548 credits |
| OpenAI API key | Slack intent routing and final source-versus-render inspection | **Done** — the key is valid; the configured model is `gpt-5.6-sol`. Property-photo fitting does not call an image provider. |
| Anthropic key | Reading requests, drafting copy, Slack change requests | **Done** — key valid |
| Droplet + SSH key | Running unattended | **Done** — `gable`, Ubuntu 24.04, 1 vCPU / 1 GB, swap active, Python 3.12.3 |
| **Google service-account JSON + Sheet and shared-drive access** | Reading the sheet — everything depends on it | **Done** — Sheet readable, shared drive writable, Slides round-trip verified; the key is present on the droplet at mode 600. |
| nginx photo host | Public image URL Slides can fetch | **Done** — the droplet serves photos over HTTP; its directory is owned by the service and deployment reasserts that ownership. |
| `channels:read` scope (optional) | Letting the checker verify the channel id | Not granted; posting does not need it |

**Every credential is now live.** The Google account was created 2026-08-10 in
its own `gable-505204` project with Sheets, Drive and Slides enabled, no project
IAM roles, and access granted purely by the two Drive shares. It has been
exercised against the real drive: create → `batchUpdate` → `replaceAllText`
(`occurrencesChanged: 1`) → `getThumbnail` at 1600px, cleaning up after itself.

`files:read` is installed and verified by the real upload above. No remaining
Slack credential change is known.

---

## 5. What is built and green

`ruff format --check`, `ruff check`, `mypy --strict`, and `pytest` are the gate.
No source file is over 800 lines. `mypy` covers `src`, `tests` and `tools`.

| Module | State |
|---|---|
| `config.py` | Done. Frozen settings, all problems collected before raising. |
| `logging_setup.py` | Done. Two-layer secret redaction, filter + formatter. |
| `listings/intake.py` + `headers.py` | Done. Header-driven parsing refuses an unrecognisable tab rather than reading fixed positions. |
| `sheets/identity.py` + `repository.py` | Done. Whole-tab reconciliation keeps stable submission identity across duplicate timestamps and row movement. |
| `db/question_store.py` + `pipeline/questions.py` | Done. Questions and outcomes are persisted before Slack delivery and retried with stable client identities. |
| `slackapp/style.py` | Done. Every outgoing message is checked against AGENTS.md §2.0 before posting. |
| Slides request builders and `pipeline/live.py` | Concrete Drive and Slides I/O is built. Each selected source is measured before it can place a property photo or deliver. |
| `tools/check_connections.py` | Done. Proves every `.env` credential live, printing identity only. |
| `deploy/gable.service` + `PROVISION.md` | **Run.** Droplet provisioned and verified; swap active. |
| `spikes/` | Findings only — `SPIKE_A.md` and `SPIKE_A_RESULT.md`. The generator and its tests were deleted once Spike A was answered. |
| Most of `src/gable/` | Built and unit-tested: the runner, orchestrator, poller, schedule, database, sheet client, enrichment, photo fitting and hosting, the edit tools, the field manifest, the image verifier, the vision check and the house style. |
| **The wiring between them** | **Built and deployed.** The production runtime constructs `Poller` and `Runner`; the Slack-free CLI performs one guarded pass. |
| The Slack photo handoff | **Built and partly verified live.** Slack receive, authenticated download, deterministic frame fitting, and the repaired publish directory have each run or been checked. A successful resumed render and final visual result still need one watched upload. |
| Small-photo fitting | The former generative enhancement module is deleted. A source needing more than 2x now stays at no more than 2x over a blurred, darkened fill derived only from that upload, with `ai_enhanced=0`; the exact Mike 275×183 to 1078×504 result was rendered and visually inspected locally. |
| Former photo-resolver and handler placeholders | Historical only. They were unreachable and have since been removed rather than advertised as built. |

`listings/intake.py` resolves the real form headers by name, including the
split-name layout on Testing_1, so a column insertion cannot silently remap a
listing field.

---

## 6. Where the build actually stands

The module graph, automatic trigger, durable Slack photo resume, core
conversational edits, and notes-aware template selector are built. The current
priority order is:

1. Pass the complete release gate, deploy with Sheet polling still disabled,
   and let the database migrate without opening listing work.
2. Have Chase confirm the seven exact pre-release responses named in the audit,
   adopt only those asserted rows, then require a read-only zero-work preview.
3. Run the watched Mike line 48 test with one new property-image upload and open
   the final editable Slides link. The test is not successful until Chase sees a
   correct flyer; a rejected draft is not delivery.
4. Baseline the live template catalogue, certify the remaining source designs,
   and enable polling only after both the zero-work preview and watched flyer
   test pass. Firecrawl, conversation, and vision calls remain under the shared
   $50 spend guard.

---

## 7. Overnight matrix test — 13–14 August 2026

Trimmed 2026-08-17 to keep this file under the 800-line ceiling. The run itself:
every person on the roster against every design, driven from `Testing_1` into
`#monarch-bot-playground`. 263 runs opened, 68 flyers delivered, $17.32 spent
against the $500 campaign ceiling. `#calvo` received nothing.

Its seven fixes are in git with their commit hashes, and the two findings worth
keeping are recorded where they are actually read: text width measured from the
designs' own faces is `CLAUDE.md` §4.3 item 9, and Slides silently stripping
U+E000 from replacement text is item 10. Its one open item — Sold overflowing
its callout — was closed by the 2026-08-16 round 9 entry above, which ran all
six designs with zero code defects.
