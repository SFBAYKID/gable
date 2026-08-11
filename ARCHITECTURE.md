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
2. **Identify.** It joins to `Sales_People` on first name, last name and email,
   resolving *Lolo Simmons → Template 1*, plus her phone number and headshot.
3. **Fetch the template.** Template 1 is copied from the shared drive.
4. **Ask for the hero image.** Gable posts in Slack — *"New listing for Lolo
   Simmons at 123 Main St. Which photo do you want as the hero?"* — and waits.
5. **Receive it.** The user drops an image into the thread.
6. **Fit the image to the frame.** The photo is reprocessed so it genuinely sits
   right in the template. This is the hardest part of the product — §4.6.
7. **Render.** Text and photo fill a copy of the template.
8. **Check its own work.** A vision pass asks whether the result actually looks
   correct — §4.7.
9. **Deliver.** Gable posts a clickable link to the finished Slides file. Carmen
   opens and edits it, or replies in the thread and Gable redoes it.

If anything is missing or malformed at any point — no phone number, no address,
a price that will not parse — Gable **asks instead of guessing** (§4.3b).

```
Real-estate agent
      │ fills out
      ▼
Google Form ─────────► Google Sheet
                            │  2 min business hours CST · 10 min otherwise
                            ▼
                 ┌──────────────────────────┐
                 │   Gable (droplet)        │
                 │  poller                  │
                 │    ↓                     │
                 │  normalize ──────────────┼─► ASK if a field is missing ─┐
                 │    ↓                     │                              │
                 │  identify agent ─────────┼─► Sales_People → template    │
                 │    ↓                     │                              │
                 │  ASK for hero image ─────┼──────────────────────────────┤
                 │    ↓                     │                              │
                 │  fit image to frame ─────┼─► Pillow, free; a model only  │
                 │    ↓                     │                              │
                 │  store photo ────────────┼─► public HTTPS URL           │
                 │    ↓                     │                              ▼
                 │  render ─────────────────┼─► Drive: copy + batchUpdate  Slack
                 │    ↓                     │                            thread
                 │  inspect the render ─────┼─► Anthropic vision           ▲
                 │    ↓                     │                              │
                 │  deliver the link ───────┼──────────────────────────────┘
                 └──────────────────────────┘
                            │
                            ▼
              Carmen clicks and edits in Slides,
              or replies "use the other photo"
```

There is no download step and no file to upload anywhere. Gable copies the
template inside the shared drive, fills it through one `batchUpdate`, and posts
Carmen a link to the finished Slides file. She edits it in place.

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
   question (`CLAUDE.md` §4.3 item 4) and it is a TypeScript codebase alongside
   the Python one.

**Google Slides does what all three were for, on infrastructure already
required.** `replaceAllShapesWithImage` swaps a shape containing the literal text
`{{hero_photo}}` for an image fetched from a public HTTPS URL — exactly the
capability Spike A proved an uploaded Canva file could never carry. It needs no
Enterprise plan, no marketplace review, and no second language, and it reuses the
same Google service account already required to read the Sheet. Bulk Create also
has no API for an agent to drive, so even the working path still needed a human
at a keyboard; Slides does not.

The cost is that Carmen edits in Slides rather than Canva. Against building the
post by hand, that is the smaller change.

### 2.2 Why Socket Mode instead of HTTP events?

Socket Mode opens an outbound WebSocket to Slack. No inbound ports, no TLS
certificate, no domain name, no reverse proxy. On a $6 / 1 GB droplet that removes
most of the operational surface.

**The tradeoff is real:** with no public endpoint, a Google Apps Script
`onFormSubmit` webhook cannot reach Gable. The Sheet must be polled instead.

At this volume — a handful of listings a day — a 180-second poll is
indistinguishable from a webhook and dramatically simpler. If volume grows to
where latency matters, revisit: add Caddy for automatic TLS and switch to HTTP
events. Do not do that work preemptively.

### 2.3 Why poll rather than use Drive change notifications?

Same reason. Push notifications need a public HTTPS endpoint.

### 2.4 Why a shared drive, not My Drive

Not a preference — a hard constraint. **A service account has a 0 GB storage
quota.** Any file it creates in its own My Drive fails with
`StorageQuotaExceeded` on the very first render. Files created inside a *shared
drive* are owned by the drive, not the account, so the quota never applies.

`config.py` rejects a non-shared drive id at startup rather than letting this
surface at runtime, on the first real listing, as an opaque Google error.

### 2.5 Why the renderer is pure and the client is not

`slides/renderer.py` takes domain objects and returns JSON-serializable request
dicts. No network, no credentials, no Google client. That split means the entire
fill behaviour is unit-tested — 36 tests today — without a service account
existing, and leaves `slides/client.py` holding nothing but I/O.

It is also why the renderer was finishable while the Google credential is still
blocked.

### 2.6 The polling schedule

Requests arrive during the working day. Polling at the same rate at 3am burns
Sheets quota to discover nothing.

| When | Interval |
|---|---|
| Mon–Fri, **07:00–17:00 US Central** | every **2 minutes** |
| All other hours, and weekends | every **10 minutes** |

Central is the operating timezone, so the window is defined there and evaluated
against a timezone-aware clock — **never against droplet-local time**, which is
UTC and would shift the business-hours window by five or six hours depending on
daylight saving. That is the kind of bug that works all winter and breaks in
March.

A request arriving at 4:58pm Friday is picked up within two minutes. One
arriving Saturday morning waits at most ten. Both are well inside the time it
takes a human to notice.

`GABLE_POLL_INTERVAL_SECONDS` is the **quiet** rate — nights and weekends — so
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

One Google Sheets workbook, four tabs. This is the whole persistence layer —
there is no database. At this volume a database would be overhead, and keeping
state in the Sheet means Carmen and Chase can inspect and correct it without
tooling.

Only `Form Responses 1` exists today. `Agents`, `Runs` and `Templates` are
additive and are waiting on decision D3 in `STATUS.md`.

### 3.1 Tab `Form Responses 1` — read only, never written

Google Forms owns this tab. Gable reads it and never modifies it.

**These are the live columns, read off the real sheet on 2026-08-10** — the sheet
is "Social Media and Marketing Request Form (Responses)", not the listing form
this document originally assumed:

| Col | Header | Notes |
|---|---|---|
| A | Timestamp | Form-generated; part of the idempotency key |
| B | Email Address | Join key into `Agents` |
| C | Name of Agent | Free text — `Agents` is the authority on display name |
| D | Service Guidelines Acknowledgment | Ignored |
| E | Select your request type | Sold, New Listing, Open House, Price Reduction, Under Contract, Client Review Post, New Listing with Open House. Scope is decision D2 |
| F | Property address | Empty on every visible row — see §5 |
| G | Select postcard category | Out of Phase 1 scope pending D2 |
| H | Upload photos | Empty on every visible row — see §5 |
| I | Upload your video assets | Out of Phase 1 scope pending D2 |

**Absent, and needed by the template:** price, description, beds/baths/sq ft, and
agent phone. Nothing in the form supplies a price the post displays. See §5.

`listings/normalize.py` keeps this mapping as data (`ColumnMap`), so a renamed or
reordered question is a configuration change rather than a code change.

### 3.2 Tab `Sales_People` — identity and template map

The join that turns "Lolo Simmons" into a template, a phone number and a
headshot. Matched on **first name, last name and email** — email alone is the
primary key, but the name columns are what a human reads when correcting a row.

**What is actually in the live tab, read 2026-08-10 through the service
account.** The tab exists and is named `Sales_People` (underscore), not
`Salespeople`. It has four columns and one data row:

| | A | B | C | D |
|---|---|---|---|---|
| row 1 | *(blank)* | | | |
| row 2 | `Email` | `First Name` | `Last Name` | `Template` |
| row 3 | `lolo@cornerhouserealty.com` | `Lolo ` | `Simmons` | `1` |

Three things in that to build against, not around:

- **The header is on row 2.** Row 1 is blank. Anything that assumes `A1` is the
  header reads an empty row and matches nothing. Find the header row by content.
- **`Lolo ` carries a trailing space.** Real data from a real form. Every join
  key gets trimmed before comparison, or Lolo never matches herself.
- **`Template` holds `1`, not a Drive file id.** It is a human's shorthand. The
  mapping from `1` to a real template file does not exist yet — that is what the
  `Templates` tab in §3.2b is for, and it is the next thing to build.

The columns below are the target shape. Everything past `Template` is absent
today and must be added before the feature that reads it ships.

| Column | Type | Notes |
|---|---|---|
| `first_name` | str | Display name on the post. Trim on read |
| `last_name` | str | |
| `email` | str | Primary key, lowercased on read |
| `phone` | str | **Not collected by the form.** Lives here so it is entered once per agent, not once per listing. Rendered as `(818) 259-7432` |
| `brokerage_url` | str | Firecrawl verification target |
| `slides_template_id` | str | Drive **file id** of that agent's template — never a filename, so renaming a file cannot silently break the mapping |
| `template_label` | str | What Carmen sees in Slack |
| `photo_folder_id` | str | Optional per-agent Drive folder |
| `headshot_url` | str | Public URL; many Corner House templates embed the agent's photo, so this is a second dynamic image alongside the hero |
| `active` | bool | Inactive agents are skipped with a Slack notice |

An unknown agent is **not** an error to swallow. Gable posts to Slack asking
which template to use and pauses that listing.

**Why the headshot is a field and not a template:** the Corner House library
bakes an agent photo into many designs. One template per agent per request type
would be ~40 × N templates and unmaintainable. Treating the headshot as a second
`replaceAllShapesWithImage` target keeps it at ~40 total.

### 3.2b Tab `Templates` — the template catalog

The index of what is available to render into. A template is a **pair**: the
filled reference design, and the blank background it was built from. Both live in
`GABLE_DRIVE_TEMPLATES_FOLDER_ID`.

| Column | Type | Notes |
|---|---|---|
| `template_id` | str | Drive file id of the blank background |
| `label` | str | What Carmen sees in Slack |
| `request_type` | str | Which form request type it serves |
| `placeholders` | str | The `{{...}}` names this template actually contains |

`placeholders` matters because `slides/renderer.py` leaves an unmatched
placeholder in place rather than blanking it — knowing what a template contains
is how a missing field is caught before Carmen sees `{{price}}` on a post.

### 3.3 Tab `Runs` — append-only log and idempotency guard

| Column | Notes |
|---|---|
| `run_id` | ULID |
| `response_row_id` | Stable identifier of the form row |
| `address` | For human scanning |
| `status` | `pending`/`needs_photo`/`needs_template`/`ready`/`delivered`/`failed` |
| `photo_source` | `form`/`drive`/`brokerage`/`web`/`carmen`/`generated` |
| `photo_url` | Final public URL |
| `ai_generated` | bool — **must be true for any synthetic image** |
| `ai_enhanced` | bool |
| `output_file` | Path or Slack file ID |
| `error` | Last error, truncated |
| `created_at` / `updated_at` | ISO 8601, UTC |

**`response_row_id` is the idempotency key.** Before processing any row, check
`Runs` for a terminal status. Without this, every poll rebuilds every flyer.

Do not derive `response_row_id` from the sheet row number — inserting or sorting
rows would silently reassign identities. Derive it from a stable tuple
(timestamp + agent email + address), hashed, and document the choice.

---

## 4. Pipeline stages

### 4.1 Poll

Read `Form Responses 1`. Diff against terminal rows in `Runs`. Emit new rows.
Bounded: never process more than `GABLE_MAX_BATCH` (default 25) per cycle, so a
backfill cannot exhaust the droplet.

### 4.2 Normalize (`listings/normalize.py`)

Raw row → `Listing`. Trim whitespace, lowercase the email, title-case the
address, parse the price into both a number and a display string, and format the
phone as **`(818) 259-7432`**.

That phone format is deliberate. E.164 (`+18182597432`) is correct for dialling
APIs and wrong for print — nobody puts a plus sign on a flyer. Gable's output is
read by a human, so the human format wins.

Validation failures do not raise. They produce a `Listing` with a populated
`problems` list, and the orchestrator decides what to do. A missing description
should not take down the process.

### 4.3b Ask for what is missing

The form does not collect a phone number, a price, or beds/baths/sqft, and its
address column is empty on every row seen so far. So "a required field is
absent" is the **normal** path, not an exception.

When a field the template needs is missing or malformed, Gable asks for that
specific field, naming the listing:

> ⚠️ **123 Main St — Lolo Simmons**
> I don't have a phone number for this listing, and the template has a spot for
> one. What should it say?

It never invents a value and never silently drops a field. Status is
`needs_info`, and the listing is **paused, not failed** — it waits indefinitely
and re-enters on `/gable run`.

Where the answer belongs to the agent rather than the listing — a phone number,
a headshot — Gable offers to write it to `Sales_People` so it is asked once ever
rather than once per listing.

### 4.3 Verify (`listings/verify.py`)

Fetch the agent's brokerage page with Firecrawl. Compare the submitted name,
email, and phone against what the site says.

**Verification advises; it never overwrites.** If the form says
`jon@example.com` and the site says `john@example.com`, Gable flags the
discrepancy in Slack and uses the form value. Silently "correcting" a contact
detail is how a flyer ships with a phone number nobody answers.

Cache results per `brokerage_url` for 24 hours. Every agent from the same
brokerage would otherwise refetch the same page.

Description length: Slides imposes no practical cap on replacement text, so the
real limit is the template's text box — text that overflows its shape does not
reflow the design, it just overruns. Truncate at a configurable
`GABLE_MAX_DESCRIPTION_CHARS` (default 400) on a word boundary and flag when
truncation happened, so Carmen knows to check the layout.

### 4.4 Ask for the hero image (`photos/resolver.py`)

**In practice this is where every listing goes.** The form's `Upload photos`
column was empty on every row observed, so the cascade in `CLAUDE.md` §8 usually
falls straight through to "ask".

Gable asks in the thread and waits:

> **New listing — Lolo Simmons, 123 Main St**
> Template 1. Which photo do you want as the hero?

Status is `needs_photo`. A photo a human supplies is **final** — never
second-guessed by a confidence score, never overwritten, never "improved" into a
different subject.

The cascade still exists for the cases where a photo *is* available. Each source
is an adapter with a uniform signature returning `PhotoResult | None`, so sources
can be reordered or disabled by configuration without touching the resolver.

Every result records its provenance. `photo_source` in `Runs` is not decoration —
it is the audit trail for how a picture ended up on a flyer.

**Never substitute a photo the resolver is not confident matches the address.** A
flyer with no photo gets caught by Carmen. A flyer with the wrong house ships.
Confidence below `GABLE_PHOTO_MIN_CONFIDENCE` routes to "ask Carmen," not to the
next source.

### 4.5 Store photo (`photos/store.py`)

Slides needs a **public HTTPS URL**. Verified against Google's API reference on
2026-08-10: max **2 kB of URL** (`GABLE_MAX_IMAGE_URL_BYTES=2048`), max **50 MB**,
max **25 megapixels**, and **PNG / JPEG / GIF only**.

Two consequences worth stating plainly, because both have bitten this design:

- **A Drive link will not work — and not for the reason we assumed.** The old
  text here said Drive fails because it requires auth. That is only half true,
  and the real answer was established by experiment on 2026-08-10:

  | URL form | Anonymous `GET` | Slides `replaceAllShapesWithImage` |
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
   not, verified live. nginx serves `/var/www/gable-photos`, `photos/store.py`
   publishes there over ssh, and it costs nothing beyond a droplet already paid
   for. A photo only has to survive one fetch, so the host needs no durability.
2. **DigitalOcean Spaces** — S3-compatible, public-read, stable URLs. Still the
   better answer if photo hosting ever outgrows one box, and `SPACES_*` remains
   in config for that day. Not needed now.
3. Cloudflare R2 — S3-compatible with a free tier; costs a second vendor.
4. Google Drive public links — **do not, and now we know why.** See the table.

Normalize before upload: convert to JPEG, cap the long edge at
`GABLE_PHOTO_MAX_EDGE_PX` (2400), strip EXIF (it can carry the photographer's GPS
coordinates), and re-encode at quality 85. **Stream to disk; never hold a
full-resolution image in memory on a 1 GB droplet.**

### 4.5b Fit the photo to the frame (`photos/fit.py`)

**This is the hardest problem in the product.** Everything else is plumbing;
this is the part that decides whether the output looks professional or obviously
machine-made.

A photo an agent shot on their phone is the wrong aspect ratio, often the wrong
exposure, and never composed for a 1080 × 1350 frame with a text panel across the
bottom third. Scaling it naively produces a stretched house, or a roofline
guillotined at the top — errors that are glaring to a client and invisible to a
script checking that the file is a valid JPEG.

So this is **reprocessing, not resizing**: crop to the frame's aspect ratio while
keeping the building intact, straighten, correct exposure, upscale if the source
is small. `GABLE_IMAGE_MODEL` (default `gpt-image-1-mini`) does the work — the
image model, deliberately, because this is the task worth spending capability on.

Strong vision is the requirement here, not strong generation. The model has to
*see* where the house is in the frame before it can decide where to cut.

**Reprocessing and generation stay on separate code paths.** Reprocessing
reshapes a real photograph of the real property. Generation invents a subject: it
is policy-gated, off by default, and always disclosed — an image model cannot
know what 123 Main St looks like, and a wrong house on marketing for a real
address is not a stylistic choice.

### 4.6 Look up template (`sheets/repository.py`)

`email` → `Sales_People` row → `slides_template_id`. Unknown agent, or `active`
false, pauses the listing as `needs_template` and asks in Slack.

### 4.7 Render (`slides/renderer.py` + `slides/client.py`)

Split deliberately — see §2.5. The renderer is pure; the client does the I/O.

**The template contract.** Text placeholders are `{{name}}` tokens in text boxes
over the background: `{{price}}`, `{{address}}`, `{{agent_name}}`, and so on. The
hero photo is a **shape containing the literal text `{{hero_photo}}`** —
`replaceAllShapesWithImage` swaps that shape for the image and scales it into the
shape's bounds preserving aspect ratio, which is why the shape's size and
position define the photo's frame.

Documentation this was verified against (2026-08-10):
<https://developers.google.com/workspace/slides/api/guides/merge> and
<https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request>.

**A placeholder present in the template but absent from the data is left as-is,
not blanked.** `unfilled_placeholders` reports them so the orchestrator can flag
the listing. The alternative — silently blanking — produces a post with an empty
price box that looks finished.

The client's sequence: copy the template file inside the shared drive, send one
`batchUpdate`, and leave the result in `GABLE_DRIVE_OUTPUT_FOLDER_ID`. One Slides
file per request, not per batch — Carmen opens a link, not a spreadsheet.

The concrete Slides operations now live in `pipeline/live.py`: template copy,
text replacement, photo placement, text fitting, thumbnail fetch, and batch
update. Pure request builders remain separate so edits can be tested without
Google credentials.

### 4.7b Inspect the render before delivering

Gable **looks at its own output.** `pages.getThumbnail` returns a PNG of the
rendered post; a vision pass over it asks the questions a script cannot:

- Does the photo actually sit correctly in the frame, or is it stretched,
  squashed, or cropped through the middle of the house?
- Is any text overflowing its box or colliding with the background art?
- Is a `{{placeholder}}` still visible anywhere on the page?

This exists because the failure mode here is *silent*. A render can succeed at
every API level — valid file, valid image, HTTP 200 throughout — and still be
obviously wrong to any human who looks at it. The API cannot tell you the
roofline is cut off. A model looking at the picture can.

A render that fails inspection is **not delivered as if it were fine.** Gable
says what looked wrong and offers to retry with different framing.

This is also why `ANTHROPIC_API_KEY` matters as much as the image key: one model
makes the picture fit, the other checks whether it did.

### 4.8 Deliver (`slackapp/`)

Post to `C0BP597644B`: a Block Kit summary per listing (address, agent, template,
photo thumbnail, provenance badge, any flags), **a link to the rendered Slides
file**, and buttons for `Approve`, `Replace photo`, `Skip`.

There is no attachment and nothing to download. The link is the deliverable —
Carmen opens the live Slides file and edits it, or replies in the thread.

Anything AI-generated gets a loud, unmissable badge. Not a footnote.

---

## 4A. Conversation design

This is what makes Gable an agent rather than a scheduled job, and it is as much
of the product as the renderer.

### 4A.1 Confirm before acting

Gable restates what it understood and waits for a yes. Ambiguity is resolved by
asking, never by taking the likely reading.

> **Carmen:** update the image
> **Gable:** Just to confirm — replace the **hero image** on 123 Main St?
> *(the large photo at the top)*
> **Carmen:** yes
> **Gable:** On it. Drop the new one here.

"Update the image" could mean the hero, the headshot, or one of three secondary
photos. Asking costs three seconds. Guessing costs a wrong post that looks right,
which is the exact failure `AGENTS.md` §5 is written against.

The rule generalises: **when Gable does not know, it asks.** It never picks the
convenient reading of an ambiguous instruction.

### 4A.2 Show that it is working

Anything slower than a moment gets a status message, updated in place, with some
personality. Silence reads as broken, and image reprocessing is genuinely slow:

> Working on it.
> One sec — fitting the image to the template.
> Shake and bake. Almost there.
> Checking how it turned out.

No emoji, here or anywhere. AGENTS.md §2.0 forbids them and `voice.violations()`
rejects them at the last gate, so a spinner written with one would never be
sent. **Not yet implemented:** `runner.say` can only post, not update in place —
it has no message timestamp to edit. Implementing this needs a second injected
callable, not just a call site.

Two rules keep this from being noise:

- **Name the real stage where possible.** "Fitting the image to the template"
  tells Carmen more than "working", and if it stalls she knows where.
- **Never let personality obscure state.** A failure is reported plainly, in
  words, with what failed. The fun is in the waiting, never in the outcome.

### 4A.3 Tools, not a script

The model is given tools — look up an agent, fetch a template, reprocess an
image, render, re-render one field, ask a question, write a value back to
`Sales_People` — and decides which to call.

That is what lets Carmen say *"make the price bigger and use the other photo"*
and have it work, without anyone having anticipated that sentence. A branching
script would need every phrasing enumerated in advance; a tool-using model needs
the tools to be correct and the intent to be confirmed.

### 4A.4 Never claim more than it did

From `AGENTS.md` §5, and it outranks everything above. If a photo's provenance is
uncertain, say so and give the confidence. If verification did not run, say so.
If something failed, name what failed.

The failure mode to design against is Gable reporting confident success on a post
that is subtly wrong, and Carmen — trusting it after fifty good runs — shipping
it without looking.

---

## 5. Open question: where do photos come from?

The form **does** have an "Upload photos" question (column H) — but it is empty on
every visible row, as is the property address in column F. Whether that is broken
required-ness or agents sending photos some other way is question Q1 in
`STATUS.md`, and the answer decides whether photo *resolution* is the core of the
product or a fallback. This is the single biggest driver of Carmen's 20 minutes
and the least settled part of this design.

Three moves, not mutually exclusive:

1. **Add a file-upload question to the Google Form.** Cheapest fix by a wide
   margin. Requires respondents to be signed into Google, which for a fixed roster
   of agents is not a real obstacle. **Recommended first move.**
2. **A shared Drive folder** where agents drop photos named by address. Looser,
   no form change, needs a naming convention that people will violate.
3. **Retrieval from the brokerage site**, then broader web. Always available as a
   fallback, never as the primary path.

Do not architect around generation as the primary source. See `CLAUDE.md` §8.

---

## 6. Failure handling

| Failure | Behavior |
|---|---|
| Sheet unreachable | Exponential backoff, alert Slack after 3 consecutive failures |
| Google token expired | Refresh; on failure alert Slack, do not crash-loop |
| Firecrawl down | Skip verification, flag the listing, continue |
| No photo found | Status `needs_photo`, ask Carmen, do not block the batch |
| Unknown agent | Status `needs_template`, ask Carmen |
| Spaces upload fails | Retry 3×, then fail that listing only |
| Slack disconnect | Bolt reconnects; log it, never exit |
| Slides `batchUpdate` rejects a request | Fail that listing, keep the copied file, report the Google error verbatim in Slack |
| Template missing a `{{...}}` the data fills | Render anyway; report the unfilled names so Carmen can check the layout |
| Image model 429 / outage | Fall back to the unreprocessed real photo; never block on an AI call |
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
- Within the Sheet, Gable still **only ever appends to `Runs`** and reads
  everything else. `Form Responses 1` is never written. That is enforced in code,
  not by permissions.
- Firewall: outbound only, plus SSH from known addresses.
- Never persist a scraped image longer than needed to upload it.

---

## 8. What is deliberately not built

Named so nobody wastes time adding them:

- **A database.** The Sheet is the datastore. Revisit above ~50 listings/day.
- **A web UI.** Slack is the interface.
- **Multi-tenant support.** One designer, one client roster.
- **Anything Canva.** The whole path is gone — see §2.1. Do not reintroduce it
  without reading `spikes/SPIKE_A_RESULT.md` first.
- **Automatic publishing anywhere.** Gable renders and posts a link; Carmen holds
  the final say. It never publishes to a social account.
- **Direct contact with real-estate agents.** Gable talks to Carmen and Chase.
- **Its own image CDN.** The droplet serves photos over http, and Slides copies
  each one into the presentation at insertion. Nothing needs to stay up.

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

Append to this table. Do not rewrite history — if a decision reverses, add a new
row explaining why. `CLAUDE.md` §2.7 makes this mandatory rather than polite.

| 2026-08-11 | A design's field set is a **per-template manifest**, not one global column list | Two rendered flyers were reviewed and their field sets differed. Treating them as one shipped a flyer carrying the literal words "Phone" and "Website". `slides/manifest.py` gives each design its own required fields and measured character budgets; a missing required field is a hard stop, not a blank. |
| 2026-08-11 | **Every image URL is proven before it is emitted**, not trusted because it ends in .jpg | One flyer put the template's own background illustration in the headshot frame because nothing looked at what was behind the link. `photos/verify.py` fetches, checks content type, dimensions and aspect band against the slot. Verified live: a 1080x1350 image is rejected for a landscape slot and accepted for a square one. |
| 2026-08-11 | The hero slot is **portrait**, not landscape | The Corner House deck is Instagram 4:5 throughout. The slot was briefly typed as landscape, which would have rejected correctly-shaped photos. |
| 2026-08-11 | Addresses are canonicalised to `street, city, ST ZIP`, and a **missing ZIP is never invented** | A flyer shipped reading "3 Nob Hill Park Dr, Reisterstown, MD" with no ZIP. `normalise_address()` reshapes what is there and refuses to guess what is not; `validate()` stops the run instead. |
| 2026-08-11 | **Template defects are logged for Carmen, never worked around in code** | Chase's instruction on reviewing two flyers: the "approch" typo, mixed typefaces, the low-contrast logo and panel misalignments belong to the design, not the pipeline. `TEMPLATE_ISSUES.md` records them. A code workaround hides the defect from the person who can fix it and breaks on the next re-export. |
| 2026-08-11 | Socket Mode connects in the background; the Sheet poller owns the main thread | `Poller.run_forever` installs signal handlers and must run on the main thread. `slackapp.runtime` opens Socket Mode with its non-blocking `connect`, then calls the generic lifecycle in `runtime.py`. Slack event handlers use separate database connections rather than sharing the poller's connection. |
| 2026-08-11 | Paused and review states suppress polling, and a submission gets at most three fresh attempts | Re-polling `needs_photo` repeated paid research and Slack questions indefinitely. Those states now resume their existing run only after a human response; `start_run` is the hard attempt ceiling. |
