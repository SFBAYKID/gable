# Gable — status, and what's needed from Chase

Last updated 2026-08-10 by the building agent.

**Phase 1 is blocked.** Not on effort — on two findings that landed today and
three decisions only Chase can make. This file is the whole picture in one page.

---

## 1. The two findings that stopped the build

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

**D1 — Which way after Spike A?** Recommendation: **(a) now, (b) next.**

- **(a) Text-only Bulk Create.** Gable fills every text field; Carmen places the
  photo. Ships on work already done, keeps Phase 1 Python-only. Saves the typing,
  not the photo hunting — and the hunting is the twenty minutes.
- **(b) Phase 2 data-connector app.** Its image cells *are* documented to take an
  external HTTPS URL. Gated on §4.3 item 4, and it is TypeScript.
- **(c) Canva Enterprise + autofill.** Real money, quote-only.

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

| Needed | For | Status |
|---|---|---|
| Slack app from `slack/manifest.yml` → bot + app tokens | Any Slack output | **Not created** |
| Google service-account JSON + Sheet shared with its `client_email` | Reading the sheet | **Not created** |
| Firecrawl API key | Agent verification | **Not obtained** |
| Spaces bucket + keys | Photo hosting | **Not created** — and D1 may moot it |
| Droplet + SSH key | Running unattended | **Not created** |

---

## 5. What is built and green

`ruff format --check`, `ruff check`, `mypy --strict`, `pytest` — **247 passing**.
No file over 800 lines.

| Module | State |
|---|---|
| `config.py` | Done. Frozen settings, all problems collected before raising. |
| `logging_setup.py` | Done. Two-layer secret redaction, filter + formatter. |
| `models.py` | Done. Domain types; a synthetic photo cannot be built unflagged. |
| `listings/normalize.py` | Done. Pure parsing; `ColumnMap` makes headers data. |
| `slackapp/blocks.py` | Done. Every AGENTS.md §2 message shape. |
| `deploy/gable.service` + `PROVISION.md` | Written, **never run** — no droplet exists. |
| `spikes/` | Spike A generator, instructions, and result. |
| Everything else in `src/gable/` | Docstring-only placeholders, blocked on D1/D2. |

`normalize.py`'s `ColumnMap` can be re-pointed at the real headers above without
touching logic — that was built before the sheet was seen, and it happens to
absorb this exact change.

---

## 6. Why the agent stopped

CLAUDE.md §2.6: *"A §4.3 unknown resolving against the design — especially Spike
A. Stop and report; do not silently redesign."* §11: never report a phase
complete while a §4.3 unknown it depends on is open.

Spike A resolved against the design. Every unwritten module is downstream of D1.
Writing them now would mean choosing (a), (b), or (c) unilaterally — which is
exactly what those rules forbid.

**One answer to D1 unblocks the rest.**
