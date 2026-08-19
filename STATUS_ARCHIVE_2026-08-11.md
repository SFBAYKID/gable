# Archived — the 2026-08-11 progress snapshot

Split out of `STATUS.md` on 2026-08-19 only because that file reached the
800-line ceiling. It was already labelled superseded where it stood; nothing
here is current, and the durable reasoning is in `DECISIONS.md`. Kept rather
than deleted because it records what was blocking and why at the time.

## The snapshot, as it stood

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
