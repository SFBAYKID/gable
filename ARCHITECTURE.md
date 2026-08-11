# ARCHITECTURE.md — Gable

System design, data model, and the reasoning behind each decision.
Read `CLAUDE.md` first. This document changes as the design changes — if you
alter a decision here, update this file in the same commit.

---

## 1. System shape

```
Real-estate agent
      │  fills out
      ▼
Google Form ──────────► Google Sheet (workbook, 3 tabs)
                              │
                              │ polled every 180s
                              ▼
                    ┌───────────────────────┐
                    │   Gable (droplet)     │
                    │                       │
                    │  poller               │
                    │    ↓                  │
                    │  normalize            │
                    │    ↓                  │
                    │  verify (Firecrawl)   │
                    │    ↓                  │
                    │  photo resolver ──────┼──► Drive / brokerage site / web
                    │    ↓                  │
                    │  photo store ─────────┼──► public HTTPS URL
                    │    ↓                  │
                    │  template lookup      │
                    │    ↓                  │
                    │  bulk export (xlsx)   │
                    └───────────┬───────────┘
                                │ Socket Mode
                                ▼
                    Slack #channel C0BP597644B
                                │
                                ▼
                       Carmen downloads file
                                │
                                ▼
                    Canva ▸ Bulk Create ▸ polish
```

Phase 2 removes the download step by replacing the xlsx with a Canva
data-connector app that serves the same rows directly into Bulk Create.

---

## 2. Why this shape

### 2.1 Why not the Canva Connect API autofill?

It was the first design considered, and it is genuinely better — the agent pushes
and a finished flyer simply appears in Carmen's Canva, zero clicks.

It requires a **Canva Enterprise** organization. The account is on Canva Teams
($30/month, 2 seats). Enterprise is quote-only and aimed at far larger
organizations. Paying enterprise pricing to automate flyers for a two-person
team is disproportionate.

Bulk Create reaches roughly the same outcome on the plan already being paid for.
The cost is one click of Carmen's. Against 20 minutes per flyer, that is noise.

**Keep `canva_autofill_spike.py` around.** If the account ever lands on
Enterprise, autofill becomes the better path and the spike is the head start.

### 2.2 Why Socket Mode instead of HTTP events?

Socket Mode opens an outbound WebSocket to Slack. No inbound ports, no TLS
certificate, no domain name, no reverse proxy. On a $4 droplet that removes most
of the operational surface.

**The tradeoff is real:** with no public endpoint, a Google Apps Script
`onFormSubmit` webhook cannot reach Gable. The Sheet must be polled instead.

At this volume — a handful of listings a day — a 180-second poll is
indistinguishable from a webhook and dramatically simpler. If volume grows to
where latency matters, revisit: add Caddy for automatic TLS and switch to HTTP
events. Do not do that work preemptively.

### 2.3 Why poll rather than use Drive change notifications?

Same reason. Push notifications need a public HTTPS endpoint.

### 2.4 Why xlsx rather than CSV?

CSV has no type information, and Bulk Create distinguishes text columns from
image columns. An xlsx lets us be explicit and avoids Excel's habit of mangling
addresses and prices on open.

**This depends on unverified item #1 in `CLAUDE.md` §4.3** — whether uploaded
files can carry image URLs at all. Resolve Spike A before writing the exporter.

---

## 3. Data model

One Google Sheets workbook, three tabs. This is the whole persistence layer —
there is no database. At this volume a database would be overhead, and keeping
state in the Sheet means Carmen and Chase can inspect and correct it without
tooling.

### 3.1 Tab `Form Responses 1` — read only, never written

Google Forms owns this tab. Gable reads it and never modifies it.

Expected columns (confirm against the live sheet before coding — **the form may
not currently collect a photo, which is the open question in §5**):

| Column | Notes |
|---|---|
| Timestamp | Form-generated |
| Agent first name | |
| Agent last name | |
| Agent email | Join key into `Agents` |
| Agent phone | |
| Property address | |
| Price | Normalize to a display string |
| Description | Cap length; see §4.3 |
| Beds / Baths / Sq ft | Optional feature fields |
| Photo upload | **May not exist yet.** See §5. |

### 3.2 Tab `Agents` — the template map

| Column | Type | Notes |
|---|---|---|
| `agent_email` | str | Primary key, lowercased on read |
| `agent_name` | str | Display name |
| `brokerage_url` | str | Firecrawl verification target |
| `canva_template_id` | str | Or a human label in Phase 1 |
| `template_label` | str | What Carmen sees in Slack |
| `photo_folder_id` | str | Optional per-agent Drive folder |
| `active` | bool | Inactive agents are skipped with a Slack notice |

An unknown `agent_email` is **not** an error to swallow. Gable posts to Slack
asking which template to use and pauses that listing.

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

Raw row → `Listing`. Trim whitespace, lowercase the email, normalize the phone
to E.164, parse the price into both a number and a display string, title-case
the address.

Validation failures do not raise. They produce a `Listing` with a populated
`problems` list, and the orchestrator decides what to do. A missing description
should not take down the process.

### 4.3 Verify (`listings/verify.py`)

Fetch the agent's brokerage page with Firecrawl. Compare the submitted name,
email, and phone against what the site says.

**Verification advises; it never overwrites.** If the form says
`jon@example.com` and the site says `john@example.com`, Gable flags the
discrepancy in Slack and uses the form value. Silently "correcting" a contact
detail is how a flyer ships with a phone number nobody answers.

Cache results per `brokerage_url` for 24 hours. Every agent from the same
brokerage would otherwise refetch the same page.

Description length: Canva `string` cells cap at 10,000 characters, which is far
beyond any real description. The practical limit is the template's text box.
Truncate at a configurable `GABLE_MAX_DESCRIPTION_CHARS` (default 400) on a word
boundary and flag when truncation happened, so Carmen knows to check the layout.

### 4.4 Resolve photo (`photos/resolver.py`)

The cascade in `CLAUDE.md` §8. Each source is an adapter with a uniform
signature returning `PhotoResult | None`, so sources can be reordered or disabled
by configuration without touching the resolver.

Every result records its provenance. `photo_source` in `Runs` is not decoration —
it is the audit trail for how a picture ended up on a flyer.

**Never substitute a photo the resolver is not confident matches the address.** A
flyer with no photo gets caught by Carmen. A flyer with the wrong house ships.
Confidence below `GABLE_PHOTO_MIN_CONFIDENCE` routes to "ask Carmen," not to the
next source.

### 4.5 Store photo (`photos/store.py`)

Canva needs an **external HTTPS URL** — verified, max 4,096 characters, JPEG /
PNG / WebP / SVG+XML / HEIC / TIFF, 50MB ceiling.

Options, in preference order:

1. **DigitalOcean Spaces** — S3-compatible, public-read, cheap, stable URLs.
   Recommended.
2. Static files served by the droplet — free, but ties image availability to
   droplet uptime and puts bandwidth on a $4 box.
3. Google Drive public links — fragile, and Drive's URL formats change.

Normalize before upload: convert to JPEG, cap the long edge at 2400px, strip
EXIF (it can contain the photographer's GPS coordinates), and re-encode at
quality 85. **Stream to disk; never hold a full-resolution image in 512MB.**

### 4.6 Look up template (`sheets/repository.py`)

`agent_email` → `Agents` row. Unknown agent, or `active` false, pauses the
listing and asks in Slack.

### 4.7 Export (`canva/bulk_export.py`)

Build one xlsx whose columns match the template's Bulk Create fields. Column
headers must match what Carmen connects in Canva — keep them stable and
documented, because renaming a header silently breaks her saved connections.

One file per batch, not per listing. Bulk Create is built for batches, and one
upload beats six.

### 4.8 Deliver (`slackapp/`)

Post to `C0BP597644B`: a Block Kit summary per listing (address, agent, template,
photo thumbnail, provenance badge, any flags), the xlsx attached, and buttons for
`Approve`, `Replace photo`, `Skip`.

Anything AI-generated gets a loud, unmissable badge. Not a footnote.

---

## 5. Open question: where do photos come from?

The current form appears **not** to collect a photo upload. That is the single
biggest driver of Carmen's 20 minutes, and it is also the least settled part of
this design.

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
- The Google service account gets **read** access to the Sheet and **write**
  access to `Runs` only. Do not grant Drive-wide scopes.
- Firewall: outbound only, plus SSH from known addresses.
- Never persist a scraped image longer than needed to upload it.

---

## 8. What is deliberately not built

Named so nobody wastes time adding them:

- **A database.** The Sheet is the datastore. Revisit above ~50 listings/day.
- **A web UI.** Slack is the interface.
- **Multi-tenant support.** One designer, one client roster.
- **Automatic publishing to Canva.** Requires Enterprise, and Carmen should hold
  the final say regardless.
- **Direct contact with real-estate agents.** Gable talks to Carmen and Chase.
- **Its own image CDN.** Spaces is sufficient.

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

Append to this table. Do not rewrite history — if a decision reverses, add a new
row explaining why.
