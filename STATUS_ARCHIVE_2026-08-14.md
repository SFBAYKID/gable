# Gable status archive — 2026-08-13 and 2026-08-14

Moved out of `STATUS.md` on 2026-08-27 to keep that file under the 800-line
ceiling. Nothing here is edited; it is the record as it was written.

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

---

Older entries (2026-08-12 and before) are in `STATUS_ARCHIVE_2026-08-12.md`.
