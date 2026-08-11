# Gable — status, and what's needed from Chase

Last updated 2026-08-11 by the building agent.

**Handing off?** The full context — architecture, completed work, the ranked
bug list, the guardrails and the order of work — is in `GABLE_HANDOFF.md` on
the Desktop. Read it before touching code.

**The automatic runtime is wired in the working tree and is not deployed yet.**
`slackapp.runtime` constructs the real Google clients, database, `Poller`, and
`Runner`; Socket Mode connects in the background while the poller runs on the
main thread. `cli.py` also runs one guarded pass locally without Slack.

The largest remaining Phase 1 gap is the Slack photo handoff. A new submission
can automatically enter the runner and pause at `needs_photo`, but a
`file_share` reply is not yet downloaded, fitted, hosted, and used to resume
that same run. The production database's backfill flag must also be verified
before the first deploy of the poller.

Proven live on two real submissions, invoked manually:

- **Row 100** (Lolo Simmons, Under Contract, no closing price) stopped and asked
  *"This one is marked under contract but there is no closing price on it. Do
  you have that?"* — and built nothing. That is the rule working.
- **Row 11** (Kelsey Mahon, Sold, $510,000) was **delivered**: template chosen
  from the request type, address and price from the form, and **4 beds, 2.5
  baths and 2,282 square feet researched from the web** — the fields nobody
  typed.

Gable also answers in Slack from the droplet under systemd, and the backfill is
adopted: 96 historical rows recorded as history, none built.

## The customer-facing gaps that remain

The vision pass, automatic text fitting, field manifest, and image URL verifier
are built. They are not yet enough to certify all 45 templates visually. Photo
placement still relies on a frame heuristic, headshot replacement is missing,
and conversational edits are currently refused honestly rather than executed.
No flyer should be called demo-ready until the real Slack upload path works and
the rendered output has been inspected.

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

**Corrected 2026-08-10, read through the service account.** The earlier note
here said the workbook held only `Form Responses 1` and `Sheet1`, and listed 9
columns. Both were wrong — that reading came from the browser, before API access
existed.

The form has **20 columns**, not 9. The full list is in `ARCHITECTURE.md` §3.1.
Three things in it change the design:

- **Photos are collected.** `Upload photos` and `Upload high-resolution property
  photos (up to 5 images)`. The hero image may often already be attached, which
  moves the photo cascade's common case from "hunt for it" to "read it".
- **Two address columns.** One for postcards, one for social. The form branches
  on `Select your request type`, serving postcards, video *and* social in a
  single submission.
- **Two price columns**, neither of them a list price: `New price (if price
  improvement)` and `Closing price (for sold posts only)`.

The second tab is **`Sales_People`**, not `Sheet1`, and it is not empty — header
on row 2, one row: `lolo@cornerhouserealty.com | Lolo  | Simmons | 1`. `Runs`
and `Templates` still do not exist.

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

**Resolved from this:** concrete Slides I/O is implemented in
`pipeline/live.py`, and the shared drive contains the 45 imported templates.

**D2 — RESOLVED.** The catalogue covers the supported social categories. Form
notes and request context decide the correct design within a category; building
that richer selector is current work.

**D3 — RESOLVED.** Derived state lives in SQLite. Gable reads form responses,
mirrors the salesperson roster, and never modifies the response tab.

## 3. Questions that shape the build

**Q1 — RESOLVED.** The form branches across request types and the eleven
relevant columns are explicitly mapped in `listings/intake.py`.

**Q2 — RESOLVED.** Public property facts are researched and cached. Closing
price and other genuinely unknowable or contradictory values are asked about.

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
| Slack app from `slack/manifest.yml` → bot + app tokens | Any Slack output | **Done** — `auth.test` ok (team Monarch, bot `@gable`); Socket Mode ticket issued |
| Firecrawl API key | Agent verification | **Done** — key valid, 2548 credits |
| OpenAI image key | Reprocessing a real photo; policy-gated generation | **Done** — key valid, **`gpt-image-2`** available (newest: `gpt-image-2-2026-04-21`) |
| Anthropic key | Reading requests, drafting copy, Slack change requests | **Done** — key valid |
| Droplet + SSH key | Running unattended | **Done** — `gable`, Ubuntu 24.04, 1 vCPU / 1 GB, swap active, Python 3.12.3 |
| **Google service-account JSON + Sheet and shared-drive access** | Reading the sheet — everything depends on it | **Done** — Sheet readable, shared drive writable, Slides round-trip verified; the key is present on the droplet at mode 600. |
| nginx photo host | Public image URL Slides can fetch | **Done** — the droplet serves photos over HTTP; the Slack upload path still needs to publish locally into that directory. |
| `channels:read` scope (optional) | Letting the checker verify the channel id | Not granted; posting does not need it |

**Every credential is now live.** The Google account was created 2026-08-10 in
its own `gable-505204` project with Sheets, Drive and Slides enabled, no project
IAM roles, and access granted purely by the two Drive shares. It has been
exercised against the real drive: create → `batchUpdate` → `replaceAllText`
(`occurrencesChanged: 1`) → `getThumbnail` at 1600px, cleaning up after itself.

The remaining credential-related Slack change is `files:read`. The manifest can
declare it in code, but Chase must approve the reinstall; the building agent
will never click OAuth or copy a token.

---

## 5. What is built and green

`ruff format --check`, `ruff check`, `mypy --strict`, and `pytest` are the gate.
No source file is over 800 lines. `mypy` covers `src`, `tests` and `tools`.

| Module | State |
|---|---|
| `config.py` | Done. Frozen settings, all problems collected before raising. |
| `logging_setup.py` | Done. Two-layer secret redaction, filter + formatter. |
| `models.py` | Done. Domain types; a synthetic photo cannot be built unflagged. |
| `listings/normalize.py` | Done. Pure parsing; `ColumnMap` makes headers data. |
| `slackapp/blocks.py` | Done. Every AGENTS.md §2 message shape. |
| `slides/renderer.py` and `pipeline/live.py` | Done for the current run path: pure request building plus concrete Drive and Slides I/O. |
| `tools/check_connections.py` | Done. Proves every `.env` credential live, printing identity only. |
| `deploy/gable.service` + `PROVISION.md` | **Run.** Droplet provisioned and verified; swap active. |
| `spikes/` | Findings only — `SPIKE_A.md` and `SPIKE_A_RESULT.md`. The generator and its tests were deleted once Spike A was answered. |
| Most of `src/gable/` | Built and unit-tested: the runner, orchestrator, poller, schedule, database, sheet client, enrichment, photo fitting and hosting, the edit tools, the field manifest, the image verifier, the vision check and the house style. |
| **The wiring between them** | **Built in the working tree, not deployed.** The production runtime constructs `Poller` and `Runner`; the Slack-free CLI performs one guarded pass. |
| The Slack photo handoff | **Not written.** Nothing receives a `file_share` event, downloads `url_private`, fits, publishes, and sets `hero_photo_url`. |
| `photos/enhance.py`, `photos/resolver.py`, `photos/sources.py`, `listings/verify.py`, `slackapp/handlers.py` | Still docstring-only placeholders. |

`normalize.py`'s `ColumnMap` can be re-pointed at the real headers above without
touching logic — that was built before the sheet was seen, and it happens to
absorb this exact change.

---

## 6. Where the build actually stands

The module graph and automatic trigger are built. The current priority order is:

1. Receive a Slack `file_share`, download it with bot authorization, fit it to
   1080 by 1350, publish it on the droplet, verify it, and resume the same run.
2. Make conversational edit tools operate on the thread's actual Slides file,
   reporting success only after Google confirms the change.
3. Replace the agent headshot and make hero-frame discovery safe across grouped
   PPTX imports.
4. Use the full notes context — including one-agent versus two-agent language —
   to select a template by purpose, and ask when intent remains ambiguous.
5. Wire the $50 spend guard at every paid call and certify all 45 templates with
   real rendered visual inspection.
