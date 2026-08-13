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

Canva was the original target and it is gone. The reasoning is worth keeping,
because it is the reason a week of the wrong work was not repeated.

Three Canva paths were considered, and each failed on something structural:

1. **Connect API autofill** — genuinely the best experience: push, and a finished
   design appears with zero clicks. It requires a **Canva Enterprise**
   organization. The account is on Canva Teams ($30/month, 2 seats), and
   Enterprise is quote-only and aimed at far larger organizations. Chase ruled it
   out on cost.
2. **Bulk Create from an uploaded file** — the fallback, and the original Phase 1
   deliverable. **Spike A proved it cannot work.** An uploaded xlsx/CSV has every
   column typed as text; only Canva's *manual* data table can carry an
   image-typed column. The file can carry every text field but not the photo —
   and the photo is the twenty minutes. See `spikes/SPIKE_A_RESULT.md`.
3. **A private data-connector app** — image cells *are* documented to accept an
   external HTTPS URL, but it is gated on an unverified marketplace-review
   question and is a TypeScript codebase alongside the Python one.

**Google Slides does what all three were for, on infrastructure already required.**
Gable measures one safe photo-frame object, deletes it, and creates an image at
the same transform from a public URL. That supplies what Spike A proved Canva
lacked and reuses the service account that reads the Sheet.

The cost is that Carmen edits in Slides rather than Canva. Against building the
post by hand, that is the smaller change.

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

`slides/edits.py`, `fields.py`, `fitting.py` and `manifest.py` take values and
return JSON-serializable Slides requests. No network, no credentials, no Google
client. `pipeline/live.py` is the only module holding both the settings and the
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
The eleven Gable reads are `Email Address`, `Name of Agent` — or a `First Name`
and `Second Name` pair — `Select your request type`, `Property Address`,
`Include details for post`, `Open house date/time`, `New price`, `Closing
price`, `Additional Notes for Social Media Team`, the buyer-or-seller side, and
`Notes`. Column A is the timestamp and is reconciliation evidence, but the live
workbook proves it is not unique: six pairs currently share timestamps.

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
flags — **`ai_generated` must be true for any synthetic image** — a failure
reason, the Slack thread it is speaking in, and UTC timestamps.

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

That phone format is deliberate. E.164 (`+18182597432`) is correct for dialling
APIs and wrong for print — nobody puts a plus sign on a flyer. Gable's output is
read by a human, so the human format wins.

Validation decisions are pure outcomes; the runner records and asks rather than
raising out of the batch.

### 4.3b Ask for what is missing

The form does not collect every field every design may need. A required field
being absent is a normal paused state, not an exception.

When a field the template needs is missing or malformed, Gable asks for that
specific field, naming the listing:

> **123 Main St — Lolo Simmons**
> I don't have a phone number for this listing, and the template has a spot for
> one. What should it say?

It never invents a value and never silently drops a field. Status is
`needs_info`, and the listing is **paused, not failed** — it waits indefinitely
and re-enters when Carmen or Chase replies in its owned thread after correcting
the form or roster source.

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

The private Slack URL is host-checked before the bot credential is attached,
downloads are capped at 25 MB, and the published derivative records
`photo_source=slack_upload`. The upload is oriented and stripped of metadata but
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
pixels, and measures the listing's actual values against the source boxes. A
result at or below 8 points stops as unreadable; every larger fitted size is
applied automatically and described only with the finished result.

`pipeline/template_triage.py` applies the same structural checks plus standard
capacity targets when a new file appears. The initial folder is adopted
silently; later file IDs that pass them receive a placeholder-aware visual check
for clipping, overlap, spacing, alignment, padding, and off-canvas artwork, then
one owned Slack thread. A reply that the source was updated reloads the same
Drive file under the native waiting state and names both measurement and visual
inspection as they run. Its persisted verdict is a real listing gate after the
catalogue is adopted; an unresolved new-file audit cannot be bypassed by selection.

Two-agent roles parse, but without a per-role object contract they stop at
`needs_template` rather than filling by page order.

### 4.7 Render (`pipeline/live.py`)

Templates may use bracketed labels, bare labels, or known sample values. The
resolver maps the source's literal text to semantic fields. Every replaced
literal must occupy its own text element because Slides replacement is substring
based; repeated standalone fields are valid and each request must report at
least one changed occurrence.

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

## 5. Where photos come from

The only connected hero source is **the ask**. Gable stops before rendering and
requests one image in the listing thread; Carmen's or Chase's reply is prepared,
published and attached to that same paused run. Form-photo selection, Drive
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
