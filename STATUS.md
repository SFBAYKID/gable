# Gable — status, and what's needed from Chase

Last updated 2026-08-10 by the building agent.

**Phase 1 is blocked on one credential and two decisions.** The Canva dead end is
resolved — Gable renders in Google Slides now (D1 below). Five of six credentials
are live and verified; the **Google service account** is the one that is not, and
nothing in the pipeline can read the Sheet without it. This file is the whole
picture in one page.

---

## 1. The two findings that reshaped the build

### Spike A FAILED — the photo cannot travel in the file

Run live in Chase's Canva account. Full writeup: `spikes/SPIKE_A_RESULT.md`.

An uploaded xlsx/CSV has **every column typed as text**. Only the *manual* data
table can carry an image-typed column. Proven by running both paths against the
same design in the same session: manual gives an image-glyph column, upload gives
`T` on all four fields including `photo_url`.

ARCHITECTURE.md §2.4 and §4.7 assumed `bulk_export.py` emits a file whose
`photo_url` column becomes the flyer's photo. **That is not possible.** The file
can carry every text field. It cannot carry the photo.

Per CLAUDE.md §2.6 this was reported, not worked around. `bulk_export.py` is
deliberately unwritten.

### The live form is not the form the spec describes

The sheet is **"Social Media and Marketing Request Form (Responses)"**
(`1HxgGAo…`). Its real columns:

| Col | Header |
|---|---|
| A | Timestamp |
| B | Email Address |
| C | Name of Agent |
| D | Service Guidelines Acknowledgment |
| E | Select your request type |
| F | Please provide the property address for the p… |
| G | Select postcard category |
| H | Upload photos |
| I | Upload your video assets (For Video Editing R… |

**Absent:** Price, Description, Beds/Baths/Sq ft, agent phone, first/last name
split. The flyer template displays a price; nothing in the form supplies one.

**Unplanned:** request *type* (Sold, New Listing, Open House, Price Reduction,
Under Contract, Client Review Post, New Listing with Open House), postcard
category, video assets.

**Also:** no `Agents` tab and no `Runs` tab exist — only `Form Responses 1` and
`Sheet1`. Columns F–I appeared empty on every visible row, including
`Upload photos`. Every visible row is struck through.

---

## 2. Decisions needed (blocking, ranked)

**D1 — RESOLVED. Neither (a), (b) nor (c): Gable renders in Google Slides.**

Canva was left behind entirely rather than worked around. The Slides API places
both the text *and* the photo — `replaceAllShapesWithImage` swaps a
`{{hero_photo}}` shape for an image fetched from a public HTTPS URL — which is
the exact capability Spike A proved an uploaded Canva file could never carry.
It needs no Enterprise plan, no marketplace review, and no TypeScript, and it
reuses the Google service account already required for the Sheet.

`src/gable/slides/renderer.py` is built and tested (36 tests, pure functions, no
I/O). `src/gable/canva/` was deleted. The options below are kept only as the
record of what was rejected:

- ~~(a) Text-only Bulk Create~~ — saves the typing, not the photo hunting, and
  the hunting is the twenty minutes.
- ~~(b) Phase 2 data-connector app~~ — gated on §4.3 item 4, and it is TypeScript.
- ~~(c) Canva Enterprise + autofill~~ — real money, quote-only.

**Still open from this:** `slides/client.py` (the I/O half — copy the template,
send the batch, export) is not written, and the flyer template with its
`{{...}}` placeholders does not exist in the shared drive yet.

**D2 — What is in scope, given request types?** Recommendation: Phase 1 handles
only `New Listing` and `Sold`; everything else is skipped with a Slack notice.
Serving postcards and video edits too is a different, larger product.

**D3 — May Gable create the `Agents` and `Runs` tabs?** Additive only.
`Form Responses 1` is never touched. Recommendation: yes — without `Runs` there
is no idempotency guard and every poll rebuilds every flyer.

## 3. Questions that shape the build

**Q1** — Why are the address and photo columns empty? Broken required-ness, or do
agents send these another way? This decides whether photo resolution is the
product's core or a fallback.

**Q2** — Where do Price and Description come from? Recommendation: add two
questions to the form. Cheapest fix by a wide margin.

**Q3** — Does strikethrough mean "already handled"? If so it is a completion
signal worth reading rather than guessing at.

## 4. Credentials — Chase only

Never entered by the agent (CLAUDE.md §3). It drives already-authenticated
browser sessions and consumes tokens from `.env`; it never types a secret.

Verified live by `.venv/bin/python tools/check_connections.py` on 2026-08-10.
That script makes a real call per credential and prints identity only, never a
value — run it after any `.env` change.

| Needed | For | Status |
|---|---|---|
| Slack app from `slack/manifest.yml` → bot + app tokens | Any Slack output | **Done** — `auth.test` ok (team Monarch, bot `@gable`); Socket Mode ticket issued |
| Firecrawl API key | Agent verification | **Done** — key valid, 2548 credits |
| OpenAI image key | Reprocessing a real photo; policy-gated generation | **Done** — key valid, `gpt-image-1` visible |
| Anthropic key | Reading requests, drafting copy, Slack change requests | **Done** — key valid |
| Droplet + SSH key | Running unattended | **Done** — `gable`, Ubuntu 24.04, 1 vCPU / 1 GB, swap active, Python 3.12.3 |
| **Google service-account JSON + Sheet and shared-drive access** | **Reading the sheet — everything depends on it** | **BLOCKING. Not created.** `/opt/gable/secrets/` exists on the droplet and is empty; no key locally either |
| Spaces bucket + keys | Photo hosting | **Not created** |
| `channels:read` scope (optional) | Letting the checker verify the channel id | Not granted; posting does not need it |

**The Google service account is now the only credential standing between the
pipeline and a live run.** `.env.example` §"Google" lists the four setup steps in
order — the two shares (Sheet *and* shared drive) are both required, because a
service account inherits nothing.

---

## 5. What is built and green

`ruff format --check`, `ruff check`, `mypy --strict`, `pytest` — **284 passing**.
No file over 800 lines (largest: `config.py` at 463). `mypy` covers `src`,
`tests` and `tools`.

| Module | State |
|---|---|
| `config.py` | Done. Frozen settings, all problems collected before raising. |
| `logging_setup.py` | Done. Two-layer secret redaction, filter + formatter. |
| `models.py` | Done. Domain types; a synthetic photo cannot be built unflagged. |
| `listings/normalize.py` | Done. Pure parsing; `ColumnMap` makes headers data. |
| `slackapp/blocks.py` | Done. Every AGENTS.md §2 message shape. |
| `slides/renderer.py` | Done. Pure `batchUpdate` builder; 36 tests. No I/O — `slides/client.py` is not written yet. |
| `tools/check_connections.py` | Done. Proves every `.env` credential live, printing identity only. |
| `deploy/gable.service` + `PROVISION.md` | **Run.** Droplet provisioned and verified; swap active. |
| `spikes/` | Findings only — `SPIKE_A.md` and `SPIKE_A_RESULT.md`. The generator and its tests were deleted once Spike A was answered. |
| Everything else in `src/gable/` | Docstring-only placeholders, blocked on D1/D2. |

`normalize.py`'s `ColumnMap` can be re-pointed at the real headers above without
touching logic — that was built before the sheet was seen, and it happens to
absorb this exact change.

---

## 6. Where the build actually stands

Spike A resolved against the design, the build stopped rather than silently
redesigning around it (CLAUDE.md §2.6), and D1 was then answered by leaving Canva
entirely for Google Slides. That unblocked the renderer, which is built.

What blocks the rest is no longer a decision — it is a credential:

1. **Google service account** (§4). Without it Gable cannot read the Sheet, so
   `sheets/client.py`, `sheets/repository.py`, the poller, and the orchestrator
   have nothing to be written against. This is the critical path.
2. **D2 and D3** still shape what those modules do — which request types are in
   scope, and whether Gable may create the `Agents` and `Runs` tabs.
3. **The Slides template** with its `{{...}}` placeholders has to exist in the
   shared drive before `slides/client.py` has anything to copy.

**Create the service account and (1) unblocks; answer D2 and D3 and the rest
follows.**
