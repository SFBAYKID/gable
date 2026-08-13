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
6. **Fit once.** Pillow crops and resizes to the measured frame. Only enlargement
   beyond 2x may use one fidelity-gated GPT Image 2 edit.
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
`Notes`. Column A is the timestamp and is part of the idempotency key.

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
atomically and refuses duplicates. A complete exact row ends validation with no
web request. When that row or one of its name, email, or direct-phone values is
missing, `agents/website.py` may fill only the blank for that run from one
exact-name profile whose submitted email appears on `cornerhouserealty.com`.
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

Do not derive `response_row_id` from the sheet row number — inserting or sorting
rows would silently reassign identities. The deployed key hashes timestamp,
email and address; the unique timestamp reconciles corrections to that prior id.

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

After the selected source is read, Firecrawl searches by address only when that
source displays a missing bed, bath, square-footage, or price field. A design
with none of those fields makes no property-research call. Only sourced,
plausible values are retained, and submitted values are never overwritten.
Results are cached by normalised address in SQLite and every paid call crosses
the shared spend guard. Contact validation is a separate free, official-domain
prerequisite: it runs only for a missing workbook value, and an unavailable,
ambiguous, or conflicting profile pauses without mutation.

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

Status is `needs_photo`. A photo a human supplies is **final** — never
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

Only a source that would need more than 2x enlargement takes the image-edit
path in `photos/enhance.py`. Gable first makes the exact deterministic frame
composition, then sends that derivative to `GABLE_IMAGE_MODEL_HQ` for
super-resolution with a preservation-only prompt. GPT Image 2 runs at high
quality and automatic high input fidelity, returns one image, and gets no automatic retry.
The output must still be large enough and remain within a low-frequency
composition distance from the supplied photo. Failure falls back to the locally
resized original; the rendered-flyer vision pass remains the final delivery
gate. A numeric seam detector was removed after calibration showed that it
could not distinguish a pasted sky from a normal roofline or treeline.

**Needs verification:** the 0.18 composition-distance threshold has unit coverage
but no watched live calibration. It is a coarse refusal layer, not certification.

The original Slack upload is never overwritten. SQLite records `ai_enhanced`
only when the model result survives those checks. The paid edit is limited to
one attempt per listing and reserves $0.25 under the shared $50 guard.

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

A render that fails inspection is not delivered as fine. Neither is a render
whose inspection was unavailable, malformed, refused, or low-confidence. The
OpenAI call shares the hard spend ledger; no Anthropic runtime path exists.

### 4.8 Deliver (`slackapp/`)

Post only to the configured Gable channel, inside the listing's owned thread: a
plain-language outcome and a descriptive link to the rendered Slides file.

There is no attachment and nothing to download. The link is the deliverable —
Carmen opens the live Slides file and edits it, or replies in the thread.

Gable never publishes or exports the file. Carmen decides what leaves the
building.

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
`slackapp/routing.py` reads the root. Only a root Gable authored or one that
explicitly mentioned Gable is owned; the bounded cache keys that decision by
channel and root timestamp.

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
The default policy is `retrieve_only`; `no_ai` additionally disables paid
enlargement of the supplied real photo.

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
| Image edit outage | Fall back to local fitting; final visual inspection still decides delivery |
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

| Date | Decision | Reason |
|---|---|---|
| 2026-08-10 | Bulk Create over Connect API autofill | Autofill needs Enterprise; Teams is the plan in hand |
| 2026-08-10 | Socket Mode over HTTP events | No TLS/domain/ports on a $4 droplet |
| 2026-08-10 | Poll the Sheet, don't webhook it | Follows from Socket Mode; fine at this volume |
| 2026-08-10 | Google Sheet as datastore | Human-inspectable; volume doesn't justify a DB |
| 2026-08-10 | Photo policy is configuration, not code | Chase hasn't decided; don't decide for him |
| 2026-08-10 | Phase 1 Python, Phase 2 TypeScript | Apps SDK is TS; prove the pipeline before adding a stack |
| 2026-08-10 | `slack-manifest.yml` moved to `slack/manifest.yml` | CLAUDE.md §6 specifies that path; README's file table said otherwise. Followed §6 and corrected the README. |
| 2026-08-10 | ruff line-length 100, not the default 88 | Annotated signatures plus the documentation-URL comments §5.3 requires do not fit in 88 without breaks that hurt readability. Prose files stay at 80. |
| 2026-08-10 | `mypy python_version = "3.11"` while developing on 3.13 | CLAUDE.md §9 sets 3.11 as the floor and the droplet's distro Python decides the ceiling. Pinning the check to the floor stops 3.12+ syntax reaching the server. |
| 2026-08-10 | Config fails only on *unsatisfiable* combinations; everything else degrades safely | A first pass rejected `GABLE_IMAGE_PROVIDER=openai` with a blank key — which is what `.env.example` ships, so the documented defaults would not boot. Phase 1 needs no image model: a missing provider means the cascade ends at "ask Carmen," which is safe. Only `generate_freely` with no usable provider is genuinely unsatisfiable and still refuses to start. |
| 2026-08-10 | Photo policy is authoritative; `GABLE_PHOTO_ENHANCE` is subordinate | `no_ai` disables enhancement whatever the flag says, instead of erroring and making the operator edit two variables to express one intent. Exposed as `Settings.enhancement_enabled`. |
| 2026-08-10 | `LOG_REDACT_SECRETS=false` is rejected outright | CLAUDE.md §3 makes redaction a mechanism, not a preference. A typo in `.env` must not be able to disarm the only thing standing between a token and journald. |
| 2026-08-10 | **Spike A FAILED — an uploaded xlsx/CSV cannot carry an image column** | Observed live: uploaded columns are all typed `T` (text), while the manual table's "Add image" produces a distinct image-typed column. §2.4 and §4.7 assumed the photo travels in the file; it cannot. **No redesign made — awaiting Chase.** See `spikes/SPIKE_A_RESULT.md`. |
| 2026-08-10 | **The live form is not the form this document describes** | The real sheet is "Social Media and Marketing Request Form (Responses)": Timestamp, Email Address, Name of Agent, Service Guidelines Acknowledgment, request type, property address, postcard category, Upload photos, video assets. No Price, Description, Beds/Baths/Sq ft, or agent phone — and no `Agents` or `Runs` tab exists. §3.1's column table is superseded. **Awaiting Chase on scope.** |
| 2026-08-10 | **REVERSES row 1: Google Slides replaces Canva entirely** | Spike A killed Bulk Create-from-file, autofill needs Enterprise, and the data-connector app is gated on an unverified review question plus a second language. Slides' `replaceAllShapesWithImage` places the photo from a public HTTPS URL — the exact capability Spike A proved a Canva upload lacks — on the service account already needed for the Sheet. Bulk Create also has no API to drive, so it always needed a human at a keyboard. `src/gable/canva/` deleted; `src/gable/slides/` added. §1, §2.1, §4.5 and §4.7 rewritten. |
| 2026-08-10 | Renderer is pure; a separate `slides/client.py` holds all I/O | Made the entire fill behaviour unit-testable (36 tests) before a Google credential existed — which is why the renderer is finished while the credential is still blocking. |
| 2026-08-10 | Drive must be a **shared drive**, rejected at startup | Service accounts have a 0 GB storage quota, so a file created outside a shared drive fails with `StorageQuotaExceeded` on the first render. Better to refuse at boot than to discover it on a real listing. |
| 2026-08-10 | "Enhancement" renamed to **reprocessing**; `ImageProvider` enum dropped | Reprocessing a real photo to fit the frame is the common case and the reason an image key exists; generation invents a subject. One OpenAI image key plus one Anthropic key replaced the provider enum — `Settings.images_available` is the single question that matters. Supersedes the `GABLE_PHOTO_ENHANCE` row above. |
| 2026-08-10 | Output canvas is Instagram Post 4:5, 1080 × 1350 | Confirmed on export from the Corner House template library. The product is social posts, not only printed flyers. |
| 2026-08-10 | The real droplet is 1 vCPU / **1 GB** ($6/mo), not the $4 / 512MB tier | Verified live: 961 MB RAM, 1 GB swap active, Python 3.12.3. `CLAUDE.md` §9 corrected. Size against 1 GB. |
| 2026-08-10 | `mypy` now covers `tools/` as well as `src` and `tests` | `tools/check_connections.py` was ~310 lines of real annotated code sitting outside the strict gate. Adding it surfaced one genuine `no-untyped-call` on `google-auth`, narrowed to a single line rather than a module exemption. |
| 2026-08-10 | **Gable is a conversational agent, not a pipeline** | §1 rewritten. It runs until it needs a human, then asks — for the hero image, for a phone number the form never collected, for confirmation it understood. AI-centric (the model chooses tools and inspects its own output) and human-centric (asks rather than guesses) are both requirements, not adjectives. New §4A covers confirmation, progress messages and tool use. |
| 2026-08-10 | Polling is schedule-aware: **2 min Mon–Fri 07:00–17:00 US Central, 10 min otherwise** | Requests arrive during the working day; polling at the same rate at 3am burns Sheets quota to find nothing. Evaluated against a timezone-aware Central clock, never droplet-local UTC — that variant works all winter and breaks in March. |
| 2026-08-10 | `Agents` tab renamed **`Salespeople`**, joined on first/last/email | Matches how Chase describes it and how a human corrects it. Gains `phone`, which the form does not collect and which belongs to the agent rather than the listing — asked once ever, not once per listing. |
| 2026-08-10 | Agent headshot is a dynamic image field, not a template variant | The Corner House library bakes an agent photo into many designs. One template per agent per request type is ~40 × N and unmaintainable; a second `replaceAllShapesWithImage` target keeps it at ~40 total. |
| 2026-08-10 | Phone renders as **`(818) 259-7432`**, not E.164 | Display format, not storage format. E.164 is right for dialling an API and wrong for print — nobody puts a plus sign on a flyer. The output is read by a human. |
| 2026-08-10 | Image model pinned to **`gpt-image-2`** via `GABLE_IMAGE_MODEL` | Verified available to the key against `/v1/models` and the images endpoint. Fitting a phone photo to a 1080×1350 frame is the hardest task in the product and gets the newest model. An earlier report claiming only `gpt-image-1` was a display bug in the checker — it sorted ascending, so `gpt-image-1` appeared newest. |
| 2026-08-10 | **Gable inspects its own render before delivering** (§4.7b) | The failure mode is silent: every API call can succeed while the roofline is cropped off. The API cannot see that; a vision pass over the rendered thumbnail can. This is why the Anthropic key matters as much as the image key — one model makes the picture fit, the other checks whether it did. |
| 2026-08-10 | Asking for missing fields is a first-class stage (§4.3b) | The form collects no price, phone or beds/baths, and its address column is empty on every observed row. "A required field is absent" is the normal path, so it gets a named status (`needs_info`), a specific question, and a pause — not a failure. |
| 2026-08-10 | **Corrects the row above:** the tab is `Sales_People`, not `Salespeople` | Read live through the service account once Google access existed. The earlier row named a tab that does not exist — the guess was close enough to look right and wrong enough to return nothing. Config now carries the literal live name. |
| 2026-08-10 | `Sales_People` headers are on **row 2**; row 1 is blank | Observed, not assumed. Any reader that treats `A1` as the header row matches nothing and reports "no agents" on a tab that has agents. The header row is found by content. |
| 2026-08-10 | Every `Sales_People` join key is **trimmed before comparison** | The one live row holds `"Lolo "` with a trailing space. Untrimmed, Lolo never matches herself and every one of her listings pauses as an unknown agent. |
| 2026-08-10 | Google service account has **no project IAM roles** | All of its access comes from two Drive shares — the intake Sheet as Editor, the `Gable` shared drive as Contributor. Its blast radius is exactly those two things and nothing else in the `monarchconnected.com` org. |
| 2026-08-10 | Contributor, not Content manager, on the shared drive | Verified capability: `canDelete: false, canTrash: true`. Gable never permanently deletes (CLAUDE.md §11), so the weaker grant is sufficient and is the one to keep. Cleanup uses `trashed: true`, never `files.delete`. |
| 2026-08-10 | The whole Slides render path is **verified live**, not assumed | Create in the shared drive → `batchUpdate` → `replaceAllText` returning `occurrencesChanged: 1` → `getThumbnail` at 1600px. The 0-quota trap of §2.4 was specifically tested and does not fire inside the shared drive. |
| 2026-08-10 | **Hero photos come from Carmen in Slack, not from the form.** | Chase's call. The form's photo columns hold Drive links in `aj@cornerhouserealty.com`'s Drive that neither Gable nor Chase can read (404), and 34 of 40 filled cells hold 3-5 URLs rather than one. Sourcing from Slack removes an access dependency on a third party and an ambiguity about which photo is the hero. It also collapses the CLAUDE.md §8 cascade to its step 5. |
| 2026-08-10 | **Slides will not fetch an image from Google Drive. Verified.** | Tested three URL forms — `uc?export=view`, `uc?export=download`, `thumbnail?id=` — against a file the service account had made world-readable and which returned valid PNG bytes to an anonymous request. All three rejected: *"There was a problem retrieving the image."* A public `picsum.photos` JPEG in the same batch was accepted with `occurrencesChanged: 1`, so the harness was sound. This was previously an assumption in §4.5; it is now a fact, and it means a separate public host is not optional. |
| 2026-08-10 | The service account **can** publish a Drive file (`role: reader, type: anyone`) | Worth recording even though it does not help here: the permission call succeeds and the file becomes anonymously fetchable. Drive is a usable public host for anything *other* than a Slides image fetch. |
| 2026-08-11 | **Reverses the Spaces row:** photos are hosted on the droplet over plain `http://` | Slides was assumed to require https. It does not — verified live, with a picsum control in the same batch. nginx on the existing droplet serves them, which removes a vendor, a credential and a monthly cost. Spaces stays in config for the day one box is not enough. |
| 2026-08-11 | Fitting a photo to the frame is **Pillow, not a model** (`photos/fit.py`) | Cropping and resizing to an aspect ratio is deterministic and free. A model is only needed to invent pixels — upscaling a source too small for the frame. This reverses §4.5b's assignment of the job to `photos/enhance.py` and `gpt-image-2`, and it takes a paid call off the common path entirely. |
| 2026-08-11 | Conversation and vision run on **`gpt-5-mini`**, images on **`gpt-image-1-mini`** | Priced from each vendor's own page. gpt-5-mini is 4x under Haiku 4.5 and 8x under Sonnet 5 on input, and this is the highest-volume path. Anthropic stays configured as the escalation if tool-picking proves weak. Estimated ~$2/month against ~$16 on Opus 5 plus gpt-image-2. |
| 2026-08-11 | The Slack house style is **enforced in code**, not documented (`slackapp/style.py`) | A guide nobody can run drifts, and this one already had: every card in `blocks.py` carried emoji, two carried red code spans, and raw HttpError text reached the channel. `violations()` now runs before a message is posted, so a breach cannot reach Carmen. |
| 2026-08-11 | `action_id` must be **unique within a Slack message** | The unknown-agent card gave every template button the same id, so Slack answered `invalid_blocks` and the card could never be posted at all. Found by posting it for real. Repeated buttons now carry a numeric suffix and handlers route on `dispatch_key`. |
| 2026-08-11 | **A local SQLite database**, not the Sheet, holds what Gable derives | The Sheet has no types, no constraints, no transactions, and no way to ask "have I built this?" without reading every row. It stays the source of truth for what agents submitted and is never written back to. SQLite rather than Postgres because a hundred submissions a month on a 1 GB droplet does not justify a second daemon to run, back up and patch. |
| 2026-08-11 | The poller **refuses to start** until the backfill is adopted | 99 historical rows are on the live sheet. A first poll treating them as new would build 99 flyers and spend real money in about eight minutes. `tools/adopt_backfill.py` marks them as history and builds none; until it has run, `new_submissions()` returns nothing and `Poller.ready()` says exactly what to run. |
| 2026-08-11 | A long-running loop, **not cron** | Cron cannot express two rates without two entries that disagree at the boundary, and it hands you a fresh process, a fresh Slack connection and a cold cache each time. A loop under systemd restarts on failure and asks the schedule how long to sleep. |
| 2026-08-11 | Beds, baths and square footage are **researched, never asked** | Chase's rule: a template must never ship with a blank that is public information. Verified live — 3 Nob Hill Park Dr returned 4 bed, 2.5 bath, 2,915 sqft, $525,000 at 0.95 confidence. Zillow is excluded by name because scraping it violates their terms (CLAUDE.md §8). |
| 2026-08-11 | The **second agent arrives as prose in column N**, not as a missing form field | Row 84 reads "Listed by: Stacey Abbott. Hosted by: Jason Vetter", and slide 28 is a two-agent Open House design carrying exactly those names. The submitter is the HOSTING agent there, so assuming the submitter holds the listing renders perfectly and is false. |
| 2026-08-11 | The orchestrator **decides but never performs** | Every step returns what happened and what should happen next; the caller does the I/O. That is what makes the whole sequence testable without Google, Slack or a paid call, and what stops a wrong decision becoming a wrong action. |

Append to this table. Do not rewrite history — if a decision reverses, add a new row explaining why. `CLAUDE.md` §2.7 makes this mandatory rather than polite.

| 2026-08-11 | A design's field set is a **per-template manifest**, not one global column list | Two rendered flyers were reviewed and their field sets differed. Treating them as one shipped a flyer carrying the literal words "Phone" and "Website". `slides/manifest.py` gives each design its own required fields and measured character budgets; a missing required field is a hard stop, not a blank. |
| 2026-08-11 | **Every image URL is proven before it is emitted**, not trusted because it ends in .jpg | One flyer put the template's own background illustration in the headshot frame because nothing looked at what was behind the link. `photos/verify.py` fetches, checks content type, dimensions and aspect band against the slot. Verified live: a 1080x1350 image is rejected for a landscape slot and accepted for a square one. |
| 2026-08-11 | The hero slot is **portrait**, not landscape | The Corner House deck is Instagram 4:5 throughout. The slot was briefly typed as landscape, which would have rejected correctly-shaped photos. |
| 2026-08-11 | Addresses are canonicalised to `street, city, ST ZIP`, and a **missing ZIP is never invented** | A flyer shipped reading "3 Nob Hill Park Dr, Reisterstown, MD" with no ZIP. `normalise_address()` reshapes what is there and refuses to guess what is not; `validate()` stops the run instead. |
| 2026-08-11 | **Template defects are logged for Carmen, never worked around in code** | Chase's instruction on reviewing two flyers: the "approch" typo, mixed typefaces, the low-contrast logo and panel misalignments belong to the design, not the pipeline. `TEMPLATE_ISSUES.md` records them. A code workaround hides the defect from the person who can fix it and breaks on the next re-export. |
| 2026-08-11 | Socket Mode connects in the background; the Sheet poller owns the main thread | `Poller.run_forever` installs signal handlers and must run on the main thread. `slackapp.runtime` opens Socket Mode with its non-blocking `connect`, then calls the generic lifecycle in `runtime.py`. Slack event handlers use separate database connections rather than sharing the poller's connection. |
| 2026-08-11 | Paused and review states suppress polling, and a submission gets at most three fresh attempts | Re-polling `needs_photo` repeated paid research and Slack questions indefinitely. Those states now resume their existing run only after a human response; `start_run` is the hard attempt ceiling. |
| 2026-08-11 | Slack hero uploads resume the **same paused run** and publish locally on the droplet | A `file_share` is accepted only in the configured channel and originating listing thread, with exactly one image. It is downloaded from a Slack-owned host with bot authorization, capped at 25 MB, fitted to 1080 by 1350, atomically written to nginx's directory, verified anonymously, and passed to `Runner.resume`; no new retry is opened. The app needs Chase to approve and reinstall the new `files:read` scope before live testing. |
| 2026-08-11 | Conversational edits resolve the Slack thread to one Slides output and report success only after Google confirms | The earlier app announced tools it never executed. Font size, colour, field correction, photo resize, element movement, and status now run through pure request builders. Zero or multiple matching elements is an explicit refusal rather than a guessed object id. |
| 2026-08-11 | Template choice reads **all notes fields** against explicit per-template purpose metadata | Request type chooses only the broad category. `slides/selection.py` combines post details, open-house details, extra notes, transaction side, and final notes to distinguish one or two agents, one or two dates, and calls to action. Functional mismatches are hard filters; a genuine tie or missing exact Drive file becomes `needs_template`. |
| 2026-08-11 | The live Slack manifest is JSON at `slack/manifest.json` | This supersedes the earlier path decision recorded above. The installed artifact and current repository file are JSON; setup documentation now names what actually exists. |
| 2026-08-11 | Hero placement uses an **explicit per-template raster-art object id**, never a largest-shape heuristic | A read-only inspection of the live imported files showed no ordinary image elements: photos and artwork arrive as shapes, and the largest text-free object can instead be a white panel or overlay. Three measured manifests name their exact removable hero layer. The 1080 by 1350 photo is inserted at full-slide bounds behind the surviving masks, which centres it without letterboxing. An unmeasured template or changed object id stops for review without sending a deletion request; the other 42 remain pending in `TEMPLATE_CERTIFICATION.md`. |
| 2026-08-11 | **Reverses the weekday-only polling window:** busy polling runs every day from 07:00 to 21:00 Central | Chase specified 7 AM Central through 7 PM Pacific, including weekends. Those endpoints are 07:00–21:00 Central because Pacific is two hours behind, and 18 of the 99 historical submissions arrived on weekends. `pipeline/schedule.py` and its DST tests already implement this; the earlier documentation was stale. |
| 2026-08-11 | Firecrawl, conversation, and visual inspection share one **hard $50 spend guard** | `spend.guarded_call` checks the cumulative SQLite ledger before the vendor, reserves a deliberately conservative per-call estimate, and records it even when a request fails after acceptance. Crossing the ceiling prevents the call. This makes the guard fail safe when exact token usage is unavailable and stops all currently connected paid paths through one mechanism. |
| 2026-08-11 | A small supplied hero photo is **upscaled automatically**, never rejected for resolution alone | Up to 2x stays on Pillow. Beyond that, one policy-gated GPT Image 2 edit restores resolution from the exact fitted composition, then a composition-distance and seam gate decide whether the derivative is faithful enough. The original Slack upload remains untouched, `ai_enhanced` is recorded only when the edit is used, a failed edit falls back to the original pixels, and the call shares both the one-image-call limit and $50 ceiling. |
| 2026-08-11 | `files:read` is installed and the private Slack download path is **verified live** | A real thread upload reached Gable's dimension check at 10:24, which requires successful `files_info` metadata and bot-authorized download. This supersedes the earlier waiting-on-reinstall status. The new AI upscale and resulting flyer remain unverified live until `e09bb27` is deployed in a watched run. |
| 2026-08-11 | The automatic upscale is deployed; publishing reasserts an **unprivileged writable photo root on every deploy** | A watched 10:51 upload reached GPT Image successfully, then the seam gate rejected the derivative and the original-photo fallback continued. Publishing exposed `/var/www/gable-photos` as root-owned even though systemd allowed the path. The directory is now `gable:gable`, and `make deploy` idempotently reasserts that owner and mode before restarting. This supersedes only the earlier row's deployment status; a completed live flyer and visual certification are still pending. |
| 2026-08-12 | The thinking indicator is a **posted, animated, then deleted message**, not an edited placeholder and not Slack's own thread status | Two designs were tried and both failed against what was asked for: an indicator that comes into the thread, runs, and goes away. A placeholder edited into the answer never goes away — it becomes the reply — and it strands permanently if the work raises, leaving a progress claim above a job that died. `assistant.threads.setStatus` looked correct and is not: it accepts `chat:write` and returns `ok`, but held open for twenty seconds on a live channel thread it rendered nothing, because that surface paints only inside an assistant-pane container. So `status.py` posts a real message, cycles its text every 1.5s, and deletes it — after the answer is posted, so no silent gap opens at the moment the wait ends. It runs on a background thread and swallows every error: a broken indicator must never affect the reply. Supersedes the update-in-place contract in §4A.2 and `AGENTS.md` §2.8. |
| 2026-08-12 | Responses columns are located by **header text**, and a tab with no recognisable header is refused | Fixed positions are only ever true of one tab. `Testing_1` splits the agent's name into `First Name` and `Second Name`, shifting every column from D rightward by one and heading row 2 under a blank row; read positionally its row 78 yields the service-guidelines paragraph as the request type, "Instagram Story" as the property address, and no price — all of which look like data downstream. `intake.columns_from_header` maps by name, `repository.find_header` locates the header row, and `maps_a_response_row` refuses a tab that names none of the email, request type and address rather than guessing. Also corrects §3.1, which described nine columns from a pre-API browser reading, and removes the `Templates` and `Runs` tab designs that D3 replaced with SQLite. |
| 2026-08-12 | The picker takes the best design **that has been imported**, unless the submission named the missing one | Only the top-ranked candidate was ever looked for in Drive, so Just Sold — which has eleven eligible designs and one imported — reported having none filed at all, in front of a customer. `rank` already drops functional mismatches rather than demoting them, so everything it returns is usable and the order is preference; an absent candidate is now skipped and the next taken, with the fallback logged. This does **not** reverse the earlier "missing exact Drive file becomes needs_template" row, it narrows it: a design that won on a cue was explicitly asked for in the notes, and substituting another answers a different question than the agent asked, so a missing cue-matched design still stops and asks. |
| 2026-08-12 | **Reverses the posted-message indicator:** every user response uses Slack's native purple thread status | Chase identified the posted animation as a regression from the native purple treatment that had visibly worked. Slack's current method reference and March 2026 scope update explicitly support channel apps through `chat:write`, including auto-opening the reply thread. The prior single live call that returned `ok` without visible output was not enough evidence to remove the product behavior. Mentions, follow-ups, edits, and photo work now share one timed status; after six seconds it reports the actual stage, and every exit clears it. |
| 2026-08-12 | **Template choice is a naming rule, not a selector.** One folder; the file's name is the form's request type | Chase set this with Carmen directly, and it replaces the scored catalogue. Templates live only in `Templates / Generic Templates`, and each is named exactly what the form calls that request type — `Sold` on the form uses a file called `Sold`. The picker matches on that name, tolerating only case and stray spacing, and refuses on a duplicate name rather than choosing. Two things drove it: Carmen maintains the designs, and a convention she can verify by looking at a folder is one she can keep correct, whereas a ranking that reads her notes is not; and the previous lookup found any presentation in the drive carrying a `gable_role` property regardless of folder, so seven of the eleven it offered were filed inside Kelsey Mahon's own folder and any agent's listing could have been rendered onto another agent's design. The obsolete catalogue, ranker, purpose resolver, and agent-override routing modules have been removed rather than left as a second unwired selection system. |
| 2026-08-12 | The form splits the agent's name into `First Name` and `Second Name` | Chase's request to the customer, so the sheet is easier to parse than a single `Name of Agent` free-text field. Already supported: `intake.columns_from_header` reads either shape, and joins the pair when there is no single name column. |
| 2026-08-12 | The roster moves out of the workbook tab and into the drive, and a **headshot is republished, never linked from Drive** | The `Sales_People` tab was deleted, so every run died on `Unable to parse range`. Contacts now come from `Agents Contact Information / Sales_Agents_Contact_Information.xlsx` and faces from `Head Shots`, each matched by name. The face cannot be handed to Slides as a Drive URL (§4.3 item 1), so it is downloaded with the service account and published through nginx like a hero photo; `publish_local` is content-addressed so each is written once. `agents/contacts.py` mirrors the workbook into the existing `salespeople` table, which left `find_salesperson` and its callers untouched. |
| 2026-08-12 | **A missing price no longer stops a sold post** — the flyer is built, the link posted, and the price offered afterwards | Chase's rule. A flyer with a photo, an agent, an address and a design should not wait on a number that can be typed into the thread in seconds. The offer is made **only when the chosen design has a price field**: the live `Sold` design carries the address and the agent card and no price at all, and offering to add one there promises something that cannot be done. |
| 2026-08-12 | Deleted every module unreachable from the entry points — about 3,000 lines of `src` and 1,600 of tests | Reachability from `slackapp.runtime`, `cli` and `tools/run_row` found thirteen orphans: `measure`, `registry`, `renderer`, `routing`, `catalog`, `blocks`, `handlers`, `quality`, `resolver`, `sources`, `verify`, `models` and `normalize` (whose one live function became `listings/headers.py`). Some were days old and never wired; keeping them meant the next agent reading a routing rule that routes nothing. `boto3` went with them, since Spaces was abandoned for nginx, and `openpyxl` became an explicit dependency. |
| 2026-08-12 | **Ordinary Slack replies are gated by thread-root ownership** | Gable answered Chase's keyword selection inside a Monarch Website Watcher thread because the handler treated every `thread_ts` as Gable context. It now answers without a repeated mention only when Gable authored the root or the root originally mentioned Gable. Direct mentions still work in foreign threads, but do not transfer ownership. Root lookup failures stay silent, and the bounded cache prevents one Slack read per later reply. |
| 2026-08-12 | The hero photo is cropped to the **frame's** shape, not the slide's canvas | `createImage` fits an image inside the box it is given rather than filling it. The upload is fitted to the slide's 1080x1350 canvas when it arrives in Slack — aspect 0.80 — and the `Sold` design's photo area is the full width by 37% of the height, aspect 2.14. The photo was therefore drawn at the band's height and centred: a narrow column of photograph with the layout showing either side and the design's angled mask exposed. It is now recropped to the measured frame's pixel size (1078 by 504 here) before placement. This affected every design whose photo area is not 4:5, which is nearly all of them. |
| 2026-08-12 | **A question means Gable is waiting.** Anything it is not waiting for is a statement, said once, at the end | A non-blocking advisory — "the address is 45 characters and this design fits about 42, shall I shorten it?" — was posted mid-build and then ignored by Gable itself, which shrank the text and delivered. Four messages reached the thread for one flyer. Advisories are now collected and folded into a single closing message with the link, phrased as what was done; the photo handoff stays silent when the run has already spoken; and the native waiting state remains through the question or outcome post, then clears. Only the states that genuinely stop — no photo, no design, an unusable address, a contradiction — ask anything. |
| 2026-08-12 | A new submission is **two messages**: the listing announced, the photo requested as a reply | The combined one-liner started no thread, and `PhotoHandoff` matches an upload to a run by the thread it arrives in — so there was nowhere valid to put the photo and the run would have waited forever. |
| 2026-08-12 | **Current-source geometry preflight precedes every copy; GPT-5.6 Sol is a fail-closed rendered gate** | Google already exposes the editable object's dimensions and transform, which is more trustworthy than inferring them from pixels. Preflight now measures this listing's actual values and supplied photo before mutation. The rendered thumbnail still catches masks, overlaps and crops that rectangles cannot; original-detail Responses output must be complete, strict, confident and positive or delivery stops. |
| 2026-08-12 | New files in `Generic Templates` receive proactive structure, capacity, and visual triage | The first scan adopts existing files without flooding Slack. A later Google Slides file is measured and, if deterministic checks pass, visually inspected in an owned thread; duplicate names and non-Slides uploads are refused. "Check the updated template" reloads the current source under the same native waiting state, while "run anyway" overrides only readable, non-structural listing warnings. |
| 2026-08-12 | A Slack upload keeps its aspect ratio until the measured frame is known | The old handoff cropped to the 4:5 canvas and placement cropped again to the template's often-wide frame, permanently deleting useful pixels. There is now one frame-aware crop, a pre-build crop-loss warning, local enlargement through 2x, and at most one fidelity-gated GPT Image 2 edit beyond it. Synthetic property generation remains unconnected and is not advertised as available. |
| 2026-08-12 | `/gable` commands queue mutating work onto the poller's owning thread | Bolt workers acknowledge immediately and may read through their own SQLite connection, but retries and same-run rechecks execute only after a successful current Sheet and roster refresh. A failed refresh defers the bounded queue on the normal schedule instead of using stale data or spinning. |
| 2026-08-12 | Only Carmen and Chase's stable Slack IDs may operate Gable | Channel checks alone let any member trigger paid calls and Drive changes. Production now requires exactly two IDs; stale OAuth client variables are rejected, and unused private-channel/interactivity capabilities were removed from the manifest. |
| 2026-08-12 | Human-owned contact sources are mirrored exactly and never web-written | The old lookup could plausibly overwrite client-facing identity, while deleting a workbook row left its old phone active locally. The roster is now replaced atomically from the current workbook; missing values pause and duplicate sources are refused. |
| 2026-08-12 | Corrected form rows preserve their deployed identity | Changing email or address changes the historical tuple hash and would otherwise reopen a finished listing. The unique form timestamp reconciles that change to the existing id, refreshes the saved payload, and makes a retry use current answers. |
| 2026-08-12 | Run transitions and paused-run claims are atomic | A current-state update without its `run_events` write made a listing unexplainable, and simultaneous human events could both build. Savepoints now bind row plus event, and only one worker can claim a paused run with its photo provenance. |
| 2026-08-12 | Two-agent requests fail closed until templates identify role-specific objects | Parsed listing and hosting roles do not prove which repeated text and portrait belongs to which person. The partial page-order path was removed; no copy is created until a certified slot contract exists. |
| 2026-08-12 | Template approval belongs to a **Drive revision**, and its Slack notice is a durable second step | The scanner compares `modifiedTime`, remeasures every changed revision, and listing clearance refuses an approval for older bytes. It persists the verdict as notification-pending before posting, so a Slack failure retries only that message rather than another paid visual call; deleting the source revokes a prior ready verdict. |
| 2026-08-12 | Replacement photos return to the source frame's **original z-order boundary** | Sending every hero to the back hid it behind imported raster art, while bringing every headshot to the front covered decorations. A new image starts frontmost; page-element order now identifies only the elements originally above the deleted frame and brings that set back to the front, leaving the replacement at the frame's former boundary while preserving relative order. |
| 2026-08-12 | Backfill, state transitions, and startup recovery are transactional | Catalogue adoption and its ready marker commit together; an old orphan marker is repaired rather than trusted. Runs interrupted by process death are marked failed on boot, row corrections reconcile by form timestamp, and duplicate active starts are refused before paid work begins. |
| 2026-08-12 | GPT-5.6 conversation tools use the **Responses API with explicit reasoning** | A live check found Chat Completions rejects Sol function tools at its default reasoning level. The Responses API is OpenAI's recommended reasoning/tool path; Gable now requests medium reasoning, parses direct function calls, fails plainly on incomplete/refused output, reserves ten conservative cents instead of the obsolete mini-model penny, and was verified live to ask whether “update the image” means the hero or headshot. |
| 2026-08-13 | **Reverses the `/gable` operator-command surface:** Slack is natural-language only | Chase rejected status, run, retry, templates, pause, and resume as user work the product should not require. The manifest no longer declares a slash command or `commands` scope; Bolt no longer registers one; the command service and poller's operator queue and pause controls are deleted. Mentions and owned-thread replies remain, including a plain-language request to reload a corrected source and continue a paused listing. |
| 2026-08-13 | **Narrows the no-web-contact rule:** validate every name, email, and direct phone before Slack; official-site fallback may fill only a workbook blank or a source-required credential for the current run | Chase's ordered workflow makes contact readiness a prerequisite rather than something discovered only if the chosen template has that field. A complete exact workbook row causes no web request unless the source requires a title the workbook does not collect. For an absent or incomplete row, or a title field such as REALTOR, Gable searches the one official Corner House Realty domain for one exact-name profile and requires the submitted email plus one direct phone in that profile's contact block. It does not overwrite the workbook or roster, never infers a credential, never substitutes an office/footer phone, and any submitted/workbook/site conflict pauses instead of being "corrected." Field-level provenance is written into the run-event detail without storing contact values there. |
| 2026-08-13 | Owned-thread clarification receives bounded Slack history and persisted listing facts | A terse answer such as “the big one” is meaningful only after Gable's prior hero-or-headshot question. The listener supplies up to twelve earlier turns plus the run's status, address, template, agent, output and photo facts. A confirmed hero replacement retains the current flyer until the new upload passes the ordinary build gates; a headshot replacement waits on `Head Shots`. |
| 2026-08-13 | Readable text overflow is corrected automatically, not presented as template work for Carmen | Exact Slides geometry determines the largest fitting size. Gable applies that font size only to fields it filled, keeps names, phones, emails and similar values on one line, reports the reduction in the final outcome, and still stops at the 8-point readability limit or when the source structure cannot be measured safely. The rendered vision inspection remains the final clipping and overlap gate. |
| 2026-08-13 | **Reverses the pre-build crop approval:** a supplied photo is fitted before Gable asks about layout | Chase's live test reached eighteen messages because every correctable fit became user work. A large center crop now becomes a truthful note in the one outcome after the build; it never asks “run anyway.” Structural defects, missing facts and unreadable text still stop, and the rendered vision inspection remains the fail-closed gate for a crop that removed important property content. |
| 2026-08-13 | A documented pre-inference image rejection can receive one **append-only operator release** | Reservations remain in the spend total and ordinary failures still consume the listing allowance. Only a human naming the exact spend row and durable evidence may append a release after the provider rejected an invalid request before model execution; runtime never does this automatically. The released reservation is excluded only from the per-listing image-operation count, and a unique database constraint plus an immediate transaction keep the replacement at one actual image-model call. |
| 2026-08-13 | GPT Image 2 final-photo edits use **high quality and a constraint-valid proportional canvas** | The Mike test exposed a client bug: rounding its 1078×504 frame to 1088×512 produced only 557,056 pixels, below the documented 655,360 minimum, so the API rejected it before inference and local stretching looked visibly pixelated. The chooser now enforces both 16-pixel edges, 3:1, 3,840-pixel, and total-pixel bounds; that frame becomes 1184×560. A vision-rejected draft stays internal rather than giving Slack a link to known-bad work. |
