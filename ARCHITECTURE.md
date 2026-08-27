# ARCHITECTURE.md — Gable
System design, data model, and the reasoning behind each decision.
Read `CLAUDE.md` first. This document changes as the design changes — if you
alter a decision here, update this file in the same commit.
---

## 1. What Gable is

**A conversational agent that happens to render posts.** Not a pipeline with a
Slack notifier bolted on.

That distinction drives the whole design. A pipeline runs to completion and
reports. Gable runs until it needs a human, then asks — for the hero image, for a
phone number the form never collected, for confirmation that it understood what
was meant. Both halves matter:

- **AI-centric** — the model reads the request, chooses which tool to call,
  inspects its own rendered output, and interprets replies in plain English. Not
  a decision tree with a model stapled to the end.
- **Human-centric** — it asks rather than guesses, confirms before it acts, shows
  what it is doing while it works, and every post passes under Carmen's eye
  before it reaches a client.

### 1.1 One listing, start to finish

An agent — say **Lolo Simmons** — submits the form.

1. **Poll.** Gable reads `Form Responses 1` on the §2.6 schedule and sees a new
   row: name, email, address, listing details.
2. **Identify and validate.** It joins to the Drive-hosted contact workbook and
   headshot folder on submitted agent identity. Before speaking in Slack it
   proves the submitted name, email and direct phone. A workbook blank, or a
   credential field such as REALTOR that the workbook does not collect, may
   fall back to one exact profile on the official Corner House Realty domain;
   conflicts pause and neither source is changed.
3. **Select and preflight.** The request type names one file in `Generic
   Templates`. Gable reloads that source and measures its fields, text capacity,
   photo frame and this listing's actual values before copying anything.
4. **Pause if needed.** Structural defects and unreadable minimum type stop.
   Ordinary text overflow and photo cropping are fitted automatically and
   reported with the one outcome after render inspection.
5. **Receive the hero image.** The user drops one image into the owned thread;
   its full composition is preserved until the exact frame is known.
6. **Fit once.** Pillow crops and resizes to the measured frame. Beyond 2x it
   contains the source over a blurred, darkened same-photo fill.
7. **Render and prove.** A copy is filled, read back, rendered, and compared
   with the supplied photo by `gpt-5.6-sol`. Unavailable or uncertain
   inspection blocks delivery.
8. **Deliver.** Gable posts a clickable link to the finished Slides file. Carmen
   opens and edits it, or replies in the thread and Gable redoes it.

If anything is missing or malformed at any point — no phone number, no address,
a price that will not parse — Gable **asks instead of guessing** (§4.3b).

```
Form → Sheet → poll → validate person and source → ask in Slack
     → supplied photo → frame-aware fit → Slides copy and fill
     → readback plus rendered vision check → editable Slides link
```

There is no download step and no file to upload anywhere: Gable copies the
template inside the shared drive, fills it through one `batchUpdate`, and links
Carmen to the finished Slides file, which she edits in place.

---

## 2. Why this shape

### 2.1 Why Google Slides, and not Canva

Canva was the original target and it is gone. Three paths were considered and
each failed on something structural: Connect API autofill needs a Canva
**Enterprise** organization and the account is on Teams; **Spike A proved Bulk
Create cannot carry the photo**, because an uploaded xlsx/CSV types every column
as text and only the manual data table can hold an image column; and a private
data-connector app is gated on marketplace review and a second language.

**The full reasoning, with the evidence, is CLAUDE.md §4 and
`spikes/SPIKE_A_RESULT.md`** — read both before re-opening the question. It is
kept there rather than here because it is the reason a week of the wrong work
was not repeated.

**Google Slides does what all three were for, on infrastructure already required.**
Gable measures one safe photo-frame object, deletes it, and creates an image at
the same transform from a public URL. The cost is that Carmen edits in Slides
rather than Canva; against building the post by hand, that is the smaller change.

### 2.2 Why Socket Mode instead of HTTP events?

Socket Mode opens an outbound WebSocket to Slack. No inbound ports, no TLS
certificate, no domain name, no reverse proxy. On a $6 / 1 GB droplet that removes
most of the operational surface.

**The tradeoff is real:** with no public endpoint, a Google Apps Script
`onFormSubmit` webhook cannot reach Gable. The Sheet must be polled instead.

At this volume — a handful of listings a day — a two-minute busy-hours poll is
indistinguishable from a webhook and dramatically simpler. If volume grows to
where latency matters, revisit: add Caddy for automatic TLS and switch to HTTP
events. Do not do that work preemptively.

Drive change notifications are ruled out for the same reason: push notifications
need a public HTTPS endpoint too.

### 2.4 Why a shared drive, not My Drive

Not a preference — a hard constraint. **A service account has a 0 GB storage
quota.** Any file it creates in its own My Drive fails with
`StorageQuotaExceeded` on the very first render. Files created inside a *shared
drive* are owned by the drive, not the account, so the quota never applies.

`config.py` rejects a non-shared drive id at startup rather than letting this
surface at runtime, on the first real listing, as an opaque Google error.

### 2.5 Why request building is pure and the client is not

`slides/edits.py`, `fields.py`, `fitting.py`, `typemetrics.py` and `manifest.py`
take values and return JSON-serializable Slides requests. No network, no
credentials, no Google client. `typemetrics.py` is why `fitting.py` can stay
pure and still be exact: it holds the measured advance width of every character
in the faces these designs use, so where a line breaks is arithmetic rather than
a call. `pipeline/live.py` is the only module holding both the settings and the
concrete clients, which keeps every decision unit-testable without a service
account existing and puts the I/O in one reviewable place.

### 2.6 The polling schedule

Requests arrive during the working day. Polling at the same rate at 3am burns
Sheets quota to discover nothing.

| When | Interval |
|---|---|
| Every day, **07:00–21:00 US Central** | every **2 minutes** |
| All other hours | every **10 minutes** |

Central is the operating timezone, so the window is defined there and evaluated
against a timezone-aware clock — **never against droplet-local time**, which is
UTC and would shift the business-hours window by five or six hours depending on
daylight saving. That is the kind of bug that works all winter and breaks in
March.

A request arriving at 4:58pm Friday or Saturday is picked up within two
minutes. Agents submit on weekends, so the busy window deliberately applies
every day.

`GABLE_POLL_INTERVAL_SECONDS` is the **quiet** rate — overnight — so
that variable keeps its meaning as "the slow path," and
`GABLE_POLL_BUSY_INTERVAL_SECONDS` is the fast one. Both are floored at 30
seconds by config, because a mistyped `1` is a busy loop against Google's quota.

Implemented in `pipeline/schedule.py` as pure functions over a caller-supplied
instant — nothing in that module reads a clock, which is what makes the daylight
saving behaviour testable rather than a thing that surfaces in March. What it
deliberately does not model: holidays, and per-agent timezones. Central is the
operating timezone, full stop.

---

## 3. Data model

There are three stores with separate ownership: the form-response tab is
read-only input, the shared drive holds templates, contacts, headshots and
editable output, and SQLite holds every derived submission, run transition,
template audit, cached fact, paid-call reservation and explicit operator release.

### 3.1 Tab `Form Responses 1` — read only, never written

Google Forms owns this tab. Gable reads it and never modifies it.

The sheet is "Social Media and Marketing Request Form (Responses)", not the
listing form this document originally assumed, and it has **20 columns** — the
nine listed here until 2026-08-12 came from a browser reading taken before API
access existed.

**Columns are found by header text, never by position** (`listings/intake.py`).
The twelve Gable reads are `Email Address`, `Name of Agent` — or a `First Name`
and `Second Name` pair — `Select your request type`, `Select social media
content type`, `Property Address`, `Include details for post`, `Open house
date/time`, `New price`, `Closing price`, `Additional Notes for Social Media
Team`, the buyer-or-seller side, and `Notes`. Column A is the timestamp and is
reconciliation evidence, but the live workbook proves it is not unique: six
pairs currently share timestamps.

**The content type decides whether Gable builds at all**, and it is checked in
the poller before any run opens. Carmen confirmed on 2026-08-17 that only the
static posts are graphics: an `Instagram Reel` or an `Instagram Story` is video
or animation her team makes by hand. Those rows are recorded `skipped` and
nothing is posted about them — Chase's instruction, and at 40 of the 112 live
rows a notice per row would be noise. `Static Instagram/Facebook Post` and
`Static Instagram Post` are the same job and both build. Anything else,
including a blank, builds: an extra design costs Carmen a glance, while a
silent skip is a request that disappears with nobody told.

The live tab has since made the same split `Testing_1` had — it asks `First Name
of Agent` and `Last Name of Agent` and has dropped its trailing `Notes` column,
so it too now sits one column right of the original transcription. Confirmed
2026-08-17. Nothing reads positions in production, which is why this cost
nothing; the fallback map in `intake.COLUMNS` documents the original shape only.

Position was the original mechanism and it broke on the first tab shaped
differently: `Testing_1` splits the agent's name across two columns, shifting
everything from D rightward by one, and puts its header on row 2 under a blank
row. Read positionally, its row 78 gives the acknowledgment paragraph as the
request type and the words "Instagram Story" as the property address — wrong in
a way that still looks like data. Two header matches are deliberately exact:
`Property Address`, because the postcard branch asks its own address question,
and `Notes`, because the social-media team's notes are a different field. A tab
whose header names none of the email, request type and address is **refused**,
not guessed at.

Beds, baths and square footage are public facts researched by address when a
design needs them. Agent phone and headshot come from the Drive roster sources.
The request type decides which form column is the price — a sold post carries a
closing price and a price improvement carries a new price.

### 3.2 The three sources in the shared drive

Everything a flyer needs that the form does not supply lives in the drive beside
the templates, and **each is found by name, never by position**:

| Folder | Holds | Matched by |
|---|---|---|
| `Generic Templates` | the designs | file name **is** the form's request type: `Sold` |
| `Head Shots` | the agents' faces | file name **is** the agent's name: `Andy Jang.jpg` |
| `Agents Contact Information` | `Sales_Agents_Contact_Information.xlsx` | submitting email |

`agents/contacts.py` reads the workbook — one sheet, `Email | First Name |
Last Name | Phone` — and mirrors it into the local `salespeople` table on every
pass. It is a **human-owned working document**: Gable mirrors its exact contents
atomically and refuses duplicates. When that row or one of its name, email, or
direct-phone values is missing, `agents/website.py` may fill only the blank for
that run from one exact-name profile whose submitted email appears on
`cornerhouserealty.com`.

A **complete** row is not assumed to be a correct one. It is cross-checked
against that same profile before it is trusted, and only its direct phone is
compared: the email is already proven twice over, and a name variant is how an
agent brands themselves rather than an error. A disagreement pauses. A site
that cannot answer yields to the workbook, because a cross-check that halts
every listing when a web request fails is worse than the defect it hunts.
When source text requires an agent title or credential the same exact profile
must supply it; Gable never infers REALTOR merely from the person's profession.
It does not write the website result into the workbook or SQLite roster, and a
conflict between submitted, workbook, and official-site values pauses rather
than selecting the value that looks most plausible.

The header row is located rather than assumed, in the workbook and on every
tab. This is not defensive habit — it is the two failures of 2026-08-12. The
form split its agent name across two columns, which shifted every column after
it and made a positional read return "Instagram Story" as a property address;
and the roster's header moved from row 2 to row 1, after which the sync stored
**nobody** for a day while every flyer quietly carried the brokerage's main
number and the design's own stock face.

**A headshot cannot be handed to Slides as a Drive URL** (§4.3 item 1 of
`CLAUDE.md`). `photos/headshots.py` downloads it with the service account and
republishes it through the droplet's nginx root, exactly as a hero photo is
published; `publish_local` is content-addressed, so each face is written once
and reused. If a recognised face frame exists and the named agent has no exact
file match, preflight pauses before copying. A flyer carrying the wrong person's
photograph is worse than an obvious missing value.

An unknown agent is **not** an error to swallow, and a roster that cannot be
read is never treated as an empty one.

### 3.3 Run records — append-only log and idempotency guard

**These live in SQLite (`db/store.py`, `db/run_store.py`, and `db/template_store.py`),
not in a sheet tab.** Decision D3: a
`Runs` tab and a `Templates` tab were both specified here and neither was built.
Derived state belongs in the database, and a design is added by dropping it in
the Generic Templates folder under the right name rather than by maintaining a
second index by hand.

A run carries its `run_id`, the `response_row_id` it belongs to, its `status`
(`pending`/`needs_photo`/`needs_info`/`needs_template`/`needs_review`/
`delivered`/`skipped`/`failed`), the chosen template, the output file and URL,
the `photo_url` with its `photo_source`, the `ai_generated` and `ai_enhanced`
flags — **`ai_generated` must be true for any synthetic image** — `awaiting_photo`,
a failure reason, the Slack thread it is speaking in, and UTC timestamps.

**`status` is not the whole of what a run is waiting for.** One batched message
routinely asks for two things at once — a design a person must widen, and the
property photo — and only one of them can be the status. The blocker wins it,
because that names work outside Slack and the reply saying it is done routes on
that state. `awaiting_photo` records the other half: whether the ask that went
out included the photograph. An upload is accepted in **any** paused state whose
run carries it, and a paused run that asked for nothing still declines a stray
image. Anything that reads `status == "needs_photo"` as "this run wants a photo"
is wrong by construction; read the flag beside it.

**`response_row_id` is the idempotency key.** Before processing any row, its
runs are checked for a terminal or paused status. Without this, every poll
rebuilds every flyer. The mutable run row and its append-only `run_events` entry
are written atomically; one paused run can be claimed by only one worker.

New rows receive a provisional id from source tab plus physical row, so even
byte-identical submissions remain distinct. Before new work is recorded, a
whole-tab reconciliation uses persisted content, location, timestamp and legacy
tuple evidence to preserve ids across edits and row movement; ambiguity fails
closed. A source-row alias ledger prevents legacy collapsed duplicates from
replaying but still detects a later identical response exactly once.

---

## 4. Pipeline stages

### 4.1 Poll

Read `Form Responses 1`. Reconcile it against the latest SQLite run per
submission and emit only work not already terminal or human-paused.
Bounded: never process more than `GABLE_MAX_BATCH` (default 25) per cycle, so a
backfill cannot exhaust the droplet.

### 4.2 Parse and normalise (`listings/intake.py`, `listings/address.py`)

Raw row becomes an `Intake`. Header mapping supplies the fields, whitespace and
email casing are normalised, the address is canonicalised without inventing a
ZIP, and the request type chooses the relevant price column.

The address tidier adds nothing and reorders nothing. It rewrites exactly one
token: a state the submission wrote out becomes the postal code every design
prints and every check downstream reads, and only when it sits where the state
belongs — before the closing ZIP, or at the end — because Maryland Avenue and
California, MD are both real.

That phone format is deliberate. E.164 (`+18182597432`) is correct for dialling
APIs and wrong for print — nobody puts a plus sign on a flyer. Gable's output is
read by a human, so the human format wins.

Validation decisions are pure outcomes; the runner records and asks rather than
raising out of the batch.

### 4.3b Ask for what is missing

The form does not collect every field every design may need. A required field
being absent is a normal paused state, not an exception.

**Everything outstanding is asked for once, in one message** (`pipeline/needs.py`,
AGENTS.md §2.7). The runner walks its checks gathering into a `needs.Needs`
rather than pausing at the first gap, then asks for the photograph and every
unsettled value together, naming the listing:

> **123 Main St — Lolo Simmons**
> Can you send me the image? I also need the price, beds and baths. Answer in
> one reply with whatever you have. Anything you leave out stays as the design's
> own placeholder for you to fill in.

That last sentence is the contract: **silence is a usable answer.** The ask
records the promise on the run itself, so a resume never repeats the question,
and a value nobody supplied leaves the design's placeholder visible rather than
stopping the flyer. The finished-flyer message then names exactly what was left
showing. It never invents a value and never silently drops a field.

Two kinds of stop are deliberately excluded from the batch, because neither is a
value a reply can supply. A **contradiction** — an address that reads as a
review link, a researched number that looks wrong — cannot be left as a
placeholder and stops on its own. A **structural stop** — no design named for
the request type, an uncertified two-agent layout, a missing headshot — keeps
its own message and status. Both are `needs_info` or `needs_template`, and the
listing is **paused, not failed**: it waits indefinitely and re-enters when
Carmen or Chase replies in its owned thread after correcting the source.

Agent details start with the Drive sources. A missing roster field may be used
from one exact official-site profile for the current run only; Gable never
substitutes the office number or writes web findings into the human-owned source.

### 4.3 Research public facts (`listings/enrich.py`)

Firecrawl searches only for a selected source's missing public field. A bounded
section proving the exact street, city, ZIP and source URL supplies values;
submitted values win. Legacy cache rows remain audit evidence but cannot build,
so one current strict lookup fills a gap or pauses. A public list price never
fills Sold's closing price or Price Reduction's new price, and each paid call
crosses the spend guard. Contact fallback separately requires one exact official
profile; unavailable, ambiguous, or conflicting evidence pauses.

There is no fixed description-length setting. The current source text box and
the actual replacement are measured before build; a visible rendered result is
checked again afterwards.

### 4.4 Ask for the hero image (`slackapp/photos.py`)

This is the only connected hero source. Gable does not choose among form, Drive,
brokerage or web candidates. It asks for exactly one image in the listing's
owned Slack thread and keeps that file as the source of truth.

**Only designs that have somewhere to put one are asked.** The manifest is the
authority: a design whose fields include `hero_photo` gets the ask, one without
never does. `designs.NO_HERO_DESIGNS` holds the measurement and `find_hero_frame`
returns None for those, so the geometric search cannot invent a well. Client
Review Post is the only one today — a testimonial has no property, and its single
image well is the agent's portrait. The ask was unconditional until 2026-08-27
while the build already guarded its hero work; see `DECISIONS.md`.

Gable asks in the thread and waits:

> **New Sold request from Lolo Simmons — 123 Main St**
> Can you send me the image?

The exact question is persisted before it is posted. Status becomes `needs_photo` only after Slack confirms its timestamp; until then the run stays in notification-pending
`needs_review`. A dedicated retry loop uses a fresh SQLite connection and remains active
when Sheet polling is off. An owned-thread upload received during the acknowledgement gap
atomically satisfies the outbox and claims the run, so no later retry posts a stale request.
A photo a human supplies is **final** — never
second-guessed by a confidence score, never overwritten, never "improved" into a
different subject.

The private Slack URL is host-checked before the bot credential is attached and
downloads are capped at 25 MB (`slackapp/uploads.py`, which is only that
boundary), and the published derivative records `photo_source=slack_upload`.
Values stated in the message carrying the photo are recorded against the
submission before the run resumes, so a corrected address in the caption is the
one the flyer is built from. The upload is oriented and stripped of metadata but
not cropped until preflight has measured the actual template frame.

### 4.5 Store photo (`photos/store.py`)

Slides needs a publicly fetchable image URL. Verified against Google's API
reference on 2026-08-10: max 2 kB of URL, 50 MB, 25 megapixels, and PNG, JPEG or
GIF. Fixed provider limits stay in the image boundary instead of operator
settings.

Two consequences worth stating plainly, because both have bitten this design:

- **A Drive link will not work — and not for the reason we assumed.** The old
  text here said Drive fails because it requires auth. That is only half true,
  and the real answer was established by experiment on 2026-08-10:

  | URL form | Anonymous `GET` | Historical Slides replacement experiment |
  |---|---|---|
  | `picsum.photos/….jpg` (control) | 200, valid JPEG | **accepted**, `occurrencesChanged: 1` |
  | `drive.google.com/uc?export=view&id=` | **200, `image/png`, valid bytes** | rejected — *"problem retrieving the image"* |
  | `drive.google.com/uc?export=download&id=` | **200, `image/png`, valid bytes** | rejected — same |
  | `drive.google.com/thumbnail?id=…&sz=w1600` | 404 | rejected — *"image was not found"* |

  The service account **can** publish a Drive file (`role: reader, type: anyone`)
  and the result **is** genuinely fetchable by an anonymous client. Slides still
  refuses it. So this is not a permissions problem that more sharing would fix —
  Slides declines to fetch from Drive, full stop. A separate public host is
  mandatory, not merely tidier. The control in the same batch rules out a broken
  test harness.

- **The URL only has to survive one moment.** Slides fetches the image once at
  insertion and stores a copy inside the presentation, so a post does not break
  later when the source URL expires. That makes short-lived hosting fine, and it
  means the host needs no durability guarantees at all.

Options, in preference order:

1. **The droplet, over plain `http://`** — in use, and the reason there is no
   critical path here any more. Slides was assumed to require https; it does
   not, verified live. nginx serves `/var/www/gable-photos`. Production writes
   there locally under the systemd unit's narrow `ReadWritePaths`; development
   may still use the SSH publisher. It costs nothing beyond a droplet already
   paid for. A photo only has to survive one fetch, so the host needs no
   durability.
2. An object store if photo hosting ever outgrows one box; none is connected.
3. Google Drive public links — **do not, and now we know why.** See the table.

Normalise before upload: convert to JPEG, apply EXIF orientation, strip metadata,
and reduce only an unnecessarily large edge. Do not crop to the slide canvas;
the exact hero frame is not known yet. Slack download size is hard-capped at 25
MB before Pillow opens it. The derivative is content-addressed and atomically
published.

### 4.5b Fit the photo to the frame (`photos/fit.py`)

**This is the hardest problem in the product.** Everything else is plumbing;
this is the part that decides whether the output looks professional or obviously
machine-made.

A photo an agent shot on their phone is the wrong aspect ratio, often the wrong
exposure, and never composed for a 1080 × 1350 frame with a text panel across the
bottom third. Scaling it naively produces a stretched house, or a roofline
guillotined at the top — errors that are glaring to a client and invisible to a
script checking that the file is a valid JPEG.

The common path is deterministic. Pillow center-crops once to the **measured
hero frame** and resamples to that frame's pixel dimensions. Up to a 2x
enlargement stays local. Crop loss above 30 percent becomes a note in the one
post-build outcome; it never creates an approval question. The rendered vision
gate still blocks delivery if the automatic crop removes important content.

When full-frame cover would exceed 2x, Pillow makes a blurred, darkened cover
from the source and centers a complete foreground copy at no more than 2x. The
result is the exact frame size, preserves every source edge, invents no property
detail, makes no provider call, and records `ai_enhanced=0`. The original Slack
upload is never overwritten; rendered vision remains the delivery gate.

Synthetic property-photo generation is not connected. The database retains the
disclosure flag required by the runtime contract, but the running system has no
generator or approval flow and never claims otherwise.

### 4.6 Look up template (`slides/selection.py`)

**One folder, and the file's name is the form's word.** Templates live only in
`Templates / Generic Templates`, and a design is named exactly what the form
calls that request type — a submission saying `Sold` uses a file called `Sold`.
Nothing is ranked, inferred from notes, or chosen between. No file with that
name pauses the listing as `needs_template` and says which name is missing.

The form's list, which is therefore also the list of template names: New
Listing, Open House, New Listing with Open House, Sold, Under Contract, Client
Review Post, Price Reduction, End of Year Brag Post, Video Editing Request,
Postcard Order.

Before a listing copy is created, `slides/preflight.py` reads the current source
object graph. It requires one slide, resolves fillable text, refuses unsafe
substring replacements, identifies one hero frame, converts its geometry to
pixels, and measures the listing's actual values against the source boxes.
`slides/measure.py` supplies those measurements — text-box geometry as Slides
renders it, including the scale an enclosing group applies — so preflight holds
only the verdicts about them.

A result at or below 8 points stops as unreadable, and so does one that falls
below 65% of the size the same-sized siblings in its row kept: a value shrunk
alone in a row reads as a caption between headlines whatever its absolute size.
Every larger fitted size is applied automatically and described only with the
finished result.

Preflight also reports a box this run would BLANK — a design that sets the open
house date and time separately, given a date with no time — so the missing part
joins the one batched ask instead of surfacing as a gap the visual gate refuses
after the copy exists.

`pipeline/template_triage.py` applies the same structural checks plus standard
capacity targets when a new file appears. The initial folder is adopted
silently; later file IDs that pass them receive a placeholder-aware visual check
for clipping, overlap, spacing, alignment, padding, and off-canvas artwork, then
one owned Slack thread. A reply that the source was updated reloads the same
Drive file under the native waiting state and names both measurement and visual
inspection as they run. `recheck_catalog` re-measures the whole folder and
answers once, which is what "I just imported new templates, can you check
again?" reaches when the thread belongs to no listing and no single design.
Re-saving an already-refused file yields the same sentence, so the scan records
the new revision and stays quiet rather than repeat a verdict somebody is acting
on. Its persisted verdict is a real listing gate after the catalogue is adopted,
except that `template_audits.blocker_kind` records WHY a design is refused and a
`visual` refusal does not stop a listing: how the artwork looks is the design
thread's question, and the finished flyer is inspected on its own render anyway.

Two-agent roles parse, but without a per-role object contract they stop at
`needs_template` rather than filling by page order.

A build, rebuild, or status question in a thread with no listing is answered by
`context.waiting_summary` — the paused listings and what each is owed.
`check_templates` re-measures the folder, so building never spends a paid sweep.

### 4.7 Render (`pipeline/live.py`, `pipeline/placement.py`)

Templates may use bracketed labels, bare labels, or known sample values. The
resolver maps the source's literal text to semantic fields. Every replaced
literal must occupy its own text element because Slides replacement is substring
based; repeated standalone fields are valid and each request must report at
least one changed occurrence.

That proof covers the design as it arrives, not what Gable writes onto it, and
the difference cost a wrong word on a real flyer: an agent whose brokerage name
ends in "Realtor" had that word rewritten by the title fill, because Under
Contract's title placeholder is the same word. So each field is filled in two
passes — literal to a private-use sentinel, then sentinel to the value — in one
atomic batch. No pass ever searches for a word, so nothing already written can
be matched again, whatever it contains.

`placement.py` holds the photo side: proving the source template is still the
audited one, deleting the sample photograph and any second layer carrying part
of it, creating the replacement at the frame's exact size and transform, and
restoring the depth the original sat at.

The live sequence is: copy inside the shared drive, replace text, read every
supplied value back verbatim, reject foreign sample contact details, replace the
measured hero frame, replace a recognisable headshot frame, shrink only changed
text that needs it, and render a thumbnail. A missing required value, failed
readback, incomplete Google reply, unsafe match, or failed headshot blocks
delivery. Pure request builders remain separate so mutations are testable
without credentials.

### 4.7b Inspect the render before delivering

Gable **looks at Google's actual output.** `pages.getThumbnail` returns a PNG;
`gpt-5.6-sol` receives both the preserved human photo and the render at original
image detail through one Responses API call and must return a strict schema. It
checks what source rectangles cannot:

- Does the photo actually sit correctly in the frame, or is it stretched,
  squashed, or cropped through the middle of the house?
- Is it still the same property and composition as the human-supplied photo?
- Is any text overflowing its box or colliding with the background art?
- Is a fillable label or sample value still visible anywhere on the page?

The model is told nothing about which fields went unanswered; its verdict is
filtered afterwards instead. A finding typed `placeholder` is dropped only when
this run deliberately left placeholders and only when the returned categories
line up one-to-one with the problems — otherwise nothing can be dropped safely
and the flyer still goes to a person. Every other finding stands, so a clipped
line, a bad crop, or the template's own sample agent still stops delivery.

This exists because the failure mode here is *silent*. A render can succeed at
every API level — valid file, valid image, HTTP 200 throughout — and still be
obviously wrong to any human who looks at it. The API cannot tell you the
roofline is cut off. A model looking at the picture can.

A render that fails inspection is not delivered as fine, nor is one whose
inspection was unavailable, malformed, refused, or low-confidence. Its strict
result names a typed remedy. Only a contradiction independently legible in the
original upload can request a replacement; any typed mixed finding and every
other result stays `needs_review`. The replacement question is written to the
same durable outbox as the initial image question, and only Slack confirmation
moves the run to `needs_photo`.

A run left in `needs_review` still accepts a replacement photo uploaded to its
thread, without any question having been asked. Review means built and withheld,
so there is no finished flyer to overwrite, and the state is reached precisely
when the photo is the problem — refusing one there was a dead end, because the
only remedy was the only thing the run would not take. The resume claim requires
the exact state the upload was accepted in, so a run that pauses for a different
reason during the source refresh still refuses a stale image.

The same argument generalises, and on 2026-08-20 it had to: a run parked in
`needs_template` refused the photo it had asked for in the same message. No
paused state has sent a flyer, so none of them has anything to overwrite. Any
paused run whose `awaiting_photo` is set accepts an upload, and the resume claim
still pins the exact state it was accepted in.

### 4.8 Deliver (`slackapp/`)

Post only to the configured Gable channel, inside the listing's owned thread.
Every question, review, failure and final linked outcome is persisted before
posting and retried by the polling-independent notification loop with one stable
Slack client identity. A verified file stays `building` until Slack confirms
the exact link; review and failure states keep their named reason while their
notice is pending. There is no attachment or export: Carmen opens the live
Slides file and decides what leaves the building. After an acknowledgement loss,
only one exact Gable-authored text in bounded root/thread history confirms it.

For two or more attempts, one summary counts only `delivered` as ready.
There is no operator console in Slack. Mentions and owned-thread replies are the
interface; polling and retry state stay internal.

---

## 4A. Conversation design

Moved to [`CONVERSATION.md`](CONVERSATION.md) on 2026-08-27, when this file
reached the 800-line ceiling for the second time — the decision log was the
first. Thread ownership, confirming before acting, showing that it is working,
tools rather than a script, and never claiming more than it did all live there.
`AGENTS.md` remains the record of what Gable actually says.

## 5. Where photos come from

The only connected hero source is **the ask**, and only for a design that has a
hero well at all — see §4.4. Gable stops before rendering and requests one image
in the listing thread; Carmen's or Chase's reply is prepared, published and
attached to that same paused run. Form-photo selection, Drive
selection, brokerage and web lookup, MLS access, and generation are not built.
Both accepted policy names use the same provider-free source-only fitting path;
neither permits paid enlargement of the supplied real photo.

---

## 6. Failure handling

| Failure | Behavior |
|---|---|
| Sheet unreachable | Log the pass failure and retry on the next scheduled pass |
| Google client failure | Record or report the affected operation; do not claim success |
| Firecrawl down | Leave public facts unresolved and pause rather than invent them |
| No photo found | Status `needs_photo`, ask Carmen, do not block the batch |
| Unknown or incomplete agent | Check one exact official-domain profile for workbook blanks; pause on unavailable, ambiguous, or conflicting evidence and never overwrite a source |
| Local photo publish fails | Keep that listing paused and report the failed stage |
| Slack disconnect | Bolt reconnects; log it, never exit |
| Slides mutation is rejected or incomplete | Stop that listing and translate the failure into plain language |
| Template field is missing or unsafe | Stop before copy and ask for a source-template correction |
| Very small supplied image | Keep the complete source at no more than 2x over a blurred, darkened same-photo fill |
| Supplied photo contradicts listing | Keep the rejected draft internal; ask once for the correct image and accept it on the same run |
| Vision unavailable or inconclusive | Status `needs_review`; never deliver as ready |
| Drive quota / non-shared drive | Refused at startup by `config.py`, not at render time |

**One listing failing must never stop the batch.** Wrap per-listing processing so
an exception marks that row failed and the loop continues.

---

## 7. Security

- SSH key auth only; password auth disabled on the droplet.
- Gable runs as an unprivileged `gable` user, not root.
- `.env` is `chmod 600`, owned by `gable`.
- Service-account JSON lives outside the repo tree.
- Secrets are redacted in logs by a filter in `logging_setup.py` — a mechanism,
  not a habit.
- The Google service account needs the `spreadsheets`, `drive` and
  `presentations` scopes — rendering means *creating* files, so read-only is not
  an option. **Access is bounded by sharing, not by scope:** the account is given
  the intake Sheet and the "Gable" shared drive explicitly, and a service account
  inherits nothing else. It can reach what it was handed and nothing more.
- The Sheet is read-only at runtime. Derived submissions, runs, transitions,
  template audits and spend live in SQLite; `Form Responses 1` is never written.
- Slack accepts only the configured channel and exactly two stable user IDs.
  Legacy OAuth client variables are rejected so Bolt cannot ignore the bot token.
- Firewall: outbound only, plus SSH from known addresses.
- Never persist a scraped image longer than needed to upload it.

---

## 8. What is deliberately not built

Named so nobody wastes time adding them:

- **A web UI.** Slack is the interface. A database *was* built — see §3.3.
- **Multi-tenant support.** One designer, one client roster.
- **Anything Canva.** The whole path is gone; read `spikes/SPIKE_A_RESULT.md`.
- **Automatic publishing anywhere.** Gable renders and posts a link; Carmen holds
  the final say. It never publishes to a social account.
- **Direct contact with real-estate agents.** Gable talks to Carmen and Chase.
- **A photo discovery cascade or synthetic-property generator.** Only the supplied Slack photo is connected.
- **Certified two-agent placement.** Roles parse, but per-role object mapping
  does not, so those requests stop before build.
- **A transition-history viewer.** `run_events` has no operator reader yet.
- **An object-store CDN.** Slides copies the droplet-hosted photo at insertion.

---

## 9. Decision log

Moved to [`DECISIONS.md`](DECISIONS.md) on 2026-08-13: this file hit the
800-line ceiling, and the log is the section that grows without end. It is
still append-only, and a design change still requires a row there in the same
commit.
