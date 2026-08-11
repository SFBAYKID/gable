# CLAUDE.md — Gable

Operating instructions for the coding agent building **Gable**, a Slack-native
agent that turns real-estate listing form submissions into finished Canva flyers.

Read this file completely before writing any code. Read `ARCHITECTURE.md` next.
`AGENTS.md` describes the runtime agent's behavior; this file describes *your*
behavior while building it.

---

## 1. What Gable is

Carmen is a designer. Real-estate agents submit listings through a Google Form.
Each submission becomes a flyer she builds by hand in Canva — roughly 20 minutes
per flyer, most of it spent hunting down a photo of the house.

Gable removes that work. It watches the Google Sheet behind the form, resolves
the listing photo, verifies the agent's contact details, selects the correct
Canva template for that agent, and produces a Canva Bulk Create payload. Carmen
opens Canva, runs Bulk Create, and polishes. Her 20 minutes becomes about one.

**Gable does not replace Carmen's judgment. It removes her typing and her photo
hunting.** Every flyer still passes through her before it reaches a client.

---

## 2. Non-negotiable rules for you, the building agent

These outrank every other instruction in this repository. If following one of
these means delivering less than was asked for, deliver less and say so.

### 2.1 Never fabricate

- Do not invent libraries, functions, API endpoints, response shapes, CLI flags,
  file paths, or configuration keys. If you have not read it in real
  documentation or observed it in a real response, you do not know it.
- Do not claim code was run, tested, linted, or debugged unless you actually
  executed it and saw the output.
- Do not present a guess as a fact. Do not present a plausible-sounding API
  shape as a verified one.
- Do not write fake test output, fake benchmarks, or fake logs into
  documentation or commit messages.

### 2.2 Label your confidence

Every non-obvious technical claim you make in code comments, docs, or chat must
be classifiable as one of:

1. **Verified** — you read it in vendor documentation, or you ran it and saw it work.
2. **Assumption** — reasonable, unconfirmed. Must be marked `# ASSUMPTION:` in code.
3. **Recommendation** — your engineering opinion. Say that it is one.
4. **Needs verification** — you do not know. Say so and say what would settle it.

Section 4 of this file is a worked example. Maintain it as you learn more.

### 2.3 Finish what you start

- Do not leave `TODO` stubs in a module you reported as complete.
- Do not mark a task done with failing tests, unresolved errors, or missing
  dependencies. If you are blocked, say precisely what blocks you.
- If you cannot complete something without guessing, stop and ask. State what
  you can do safely, what you cannot verify, and what input you need.

### 2.4 Surface ambiguity instead of resolving it silently

If a requirement has more than one reasonable reading, do not quietly pick the
convenient one. Name both readings and ask, or implement the conservative one
and flag it clearly in your summary.

### 2.5 Hunt for your own bugs

Before reporting any module complete, actively look for: off-by-one errors,
unhandled `None`, empty-collection cases, timezone assumptions, retry storms,
unbounded memory growth, partial-failure states, and race conditions between
the poller and the Slack handler. Write down what you checked.

### 2.6 Autonomy — decide, don't ask

**Never ask permission to fix a bug. Find it, fix it, test it, mention it in
your summary.** The answer to "should I fix this?" is always yes. Asking wastes
a round trip and reads as though you need supervision to do your job.

This is not in tension with §2.3 and §2.4 — those are about *unknowns*, this is
about *work*. The line is whether the decision is yours to make.

**Just do these. Never ask:**

- Fix any bug you find, including ones outside the task you were given.
- Refactor a file crossing 500 lines.
- Add missing tests, type annotations, docstrings, or error handling.
- Choose names, module boundaries, and internal structure.
- Pick a well-established library over hand-rolling (`httpx`, `tenacity`,
  `pydantic`, `slack_bolt`, `openpyxl`). Note the choice; don't ask about it.
- Add logging, timeouts, retries, and backoff.
- Fix formatting, lint errors, and import order.
- Correct anything in `ARCHITECTURE.md` or `.env.example` that the code
  contradicts — then update the doc in the same commit.
- Write the `Makefile`, `.gitignore`, `pyproject.toml`, and systemd unit.
- Delete dead code you wrote.

**Always ask. These are Chase's calls, not yours:**

- Anything requiring a credential, or any step that would have you enter one.
- Spending money that isn't already budgeted, or raising a rate ceiling.
- Anything irreversible: deleting data, mutating the form-responses tab,
  force-pushing, destroying a droplet.
- A §4.3 unknown resolving *against* the design — especially Spike A. Stop and
  report; do not silently redesign.
- A change to what the product *is*: scope, the photo policy default, which
  channel Gable posts to, adding a database.
- A genuine 50/50 product decision where both paths are defensible and the
  choice is about Carmen's workflow rather than about code.

**When you must ask, ask once, in a batch, with a recommendation.** Do not
drip-feed questions across ten messages. Gather what you need, present it
together, say which option you'd pick and why, and keep working on anything
that isn't blocked while you wait.

### 2.7 Leave the documentation true

**A task is not finished until the documents that describe it are correct
again.** Stale documentation is worse than missing documentation: the next agent
reads it, believes it, and builds on something that is no longer real. This has
already cost this project once — `ARCHITECTURE.md` described a Canva Bulk Create
export for days after the code had moved to Google Slides.

So: **before you report any work complete, re-read the docs your change touched
and update the ones your change made false.** In the same commit, not later.

| If you changed… | Update, in the same commit |
|---|---|
| A design decision, or a tradeoff | `ARCHITECTURE.md` — the affected section **and** a new row in the §9 decision log |
| A module, a package, or a file's home | The `CLAUDE.md` §6 layout tree |
| Anything the runtime agent says or does | `AGENTS.md` |
| A variable the code reads | `.env.example`, with the comment explaining it |
| Setup, deploy, or operations | `README.md` |
| What is blocked, decided, or waiting on Chase | `STATUS.md` |
| A §4.3 unknown you resolved | §4 of this file, moving it to §4.1/§4.2 with its evidence |

Rules that make this work:

- **Never rewrite history.** The decision log is append-only. If a decision
  reverses, add a row saying so and why — do not edit the old row away.
- **Correct the doc, don't work around it.** If the code contradicts
  `ARCHITECTURE.md`, one of them is wrong. Decide which, fix that one, and say
  in your summary which you changed.
- **Delete what is dead.** A section describing a path that no longer exists
  gets removed, not left with a note. Keep findings that explain *why* something
  was abandoned — those are the reason the next agent doesn't repeat the work.
- **This is §2.6 work, not a §2.6 question.** Updating a doc your own change
  falsified is yours to do. You never need permission for it.
- Say in your summary which documents you touched and why. "No doc changes
  needed" is a valid answer — but it means you checked, not that you forgot.

---

## 3. Credentials — read this twice

Chase has offered logins for Canva, DigitalOcean, Slack, and Google.

**You must never type a password, passphrase, 2FA code, or API secret into any
website, form, or browser session.** Not to be helpful, not to unblock yourself,
not because you were told it is fine.

The division of labor is fixed:

| Task | Who does it |
|---|---|
| Create the Slack app, click through OAuth, copy tokens | Chase |
| Create the Google service account, download the JSON key | Chase |
| Share the Google Sheet with the service account email | Chase |
| Create the DigitalOcean droplet, add an SSH public key | Chase |
| Log into Canva in a browser | Chase |
| Everything downstream of a token already in `.env` | You |

You consume secrets from `.env`. You never acquire them.

**Additional hard rules:**

- `.env` is in `.gitignore` from the first commit. Verify this before commit #1.
- Never log a secret. Redact tokens in all log output — build the redaction into
  `logging_setup.py`, do not rely on remembering.
- Never print a full service-account JSON, even in a stack trace.
- Droplet access is **SSH key only**. Disable password authentication. Do not
  handle or request Chase's DigitalOcean account password.
- If you find yourself about to paste a credential anywhere, stop and ask.

---

## 4. What is actually known about Canva

This section is the most valuable thing in this file. It was established by
direct observation and documentation reading on 2026-08-10. Respect the
confidence labels — several plausible-sounding things here are **not** confirmed,
and building on them without checking will waste days.

### 4.1 Verified by direct observation in Chase's live Canva account

- The account is on **Canva Teams**, $30/month, 2 members.
- **Bulk Create opens with no upgrade wall** on this plan.
- Bulk Create's "Select data source" panel lists working data-connector apps:
  Google Sheets, Google Analytics, Meta SKU Catalog, BigQuery, Snowflake,
  HubSpot Data, QrDy Dynamic QR, SheetSync.
- Bulk Create's manual data table has **"Add text" and "Add image"** column
  buttons. Adding an image column produces a column carrying an image-type icon,
  distinct from the `T` on text columns. **Image fields are first-class in Bulk
  Create.**
- Private apps are available on this Teams plan. Canva's team settings page
  reads: "Apps marked as Live will be available to all members of this team."

### 4.2 Verified from Canva's published documentation

- Connect API **Autofill requires a Canva Enterprise organization**. Paid plans
  get a limited development trial; the autofill response carries a
  `trial_information` object with `uses_remaining` and `upgrade_url`.
- Connect API OAuth: authorize at `https://www.canva.com/api/oauth/authorize`,
  token at `POST https://api.canva.com/rest/v1/oauth/token`, PKCE with `S256`,
  HTTP Basic client auth, access token lifetime 14400s, **refresh tokens are
  single-use** and must be rotated on disk every refresh.
- Apps SDK data-connector `DataTable` cell types: `string` (max 10,000 chars),
  `number`, `date`, `boolean`, `media`.
- A `media` image cell is `{ type: 'image_upload', url, thumbnailUrl, mimeType }`
  where `url` is an **external HTTPS URL or data URL, max 4,096 characters**;
  mime types JPEG, PNG, WebP, SVG+XML, HEIC, TIFF; max file size 50MB. There is
  an `ai_disclosure` field with values `app_generated` or `none`.
- Private Connect API **integrations** are Enterprise-only. (Distinct from
  private Apps SDK **apps**, which Teams has — see 4.1.)
- Canva's data-connector docs list supported plans as "Canva Business, Canva
  Enterprise, Canva for Education, Canva for Nonprofits." **This contradicts the
  live observation in 4.1.** Trust the observation; note the discrepancy.

### 4.3 NOT verified — spike these before building on them

Do not treat any of these as true until you have checked them yourself.

1. **Whether an uploaded CSV/XLSX can carry image URLs.** Only the *manual*
   data-entry table was observed to support image columns. Phase 1's entire
   output format depends on this. **This is Day-1 Spike A and it gates
   everything.** If uploaded files cannot carry image URLs, Phase 1's deliverable
   changes shape and you must stop and report before writing the exporter.
2. Bulk Create's maximum row count per batch.
3. Whether Bulk Create respects brand-template layout and how it handles text
   that overflows its box.
4. Whether a private data-connector app can be released to a Teams team without
   passing Canva's marketplace review. **Gates all of Phase 2.**
5. The actual numeric autofill trial quota. A spike script exists
   (`canva_autofill_spike.py`) that prints it. It compiles and its pure functions
   are unit-checked, but **it has never been run against Canva's API** — its
   response-shape assumptions are unverified by design.

---

## 5. Code standards

### 5.1 File size

- **Target: 300–500 lines per file.**
- At 500 lines, stop and refactor. A file crossing 500 is a design signal.
- **Hard ceiling: 800 lines.**
- **Never exceed 1000 lines under any circumstance.**

Line counts include docstrings and comments. Splitting a file to game the count
while leaving the logic tangled is worse than a long file — refactor the seam,
not the line count.

### 5.2 Type annotations

- Every function, method, and module-level constant is annotated. No exceptions.
- No bare `Any` without a comment explaining why a real type is impossible.
- `from __future__ import annotations` at the top of every module.
- Run `mypy --strict`. It must pass. If a third-party library lacks stubs, add a
  narrow `ignore_missing_imports` entry for that module only, never globally.

### 5.3 Documentation

- Module docstring on every file: what it does, what it assumes, what it does
  not handle.
- Docstring on every public function: purpose, arguments, return value, and
  every exception it can raise.
- Comments explain **why**, not what. `# increment i` is noise. `# Canva caps
  image URLs at 4096 chars, so we shorten before emitting` is useful.
- Every assumption gets an `# ASSUMPTION:` comment naming what would confirm it.
- Every external API call carries a comment linking the documentation page that
  describes its contract.

### 5.4 Structure

- Pure functions where possible; isolate I/O at the edges.
- No business logic inside Slack handlers — handlers parse and delegate.
- No network calls inside constructors.
- Configuration is read once at startup into a frozen dataclass. No scattered
  `os.environ` reads.
- Every external call has an explicit timeout. Every retry has a bounded budget
  and jittered backoff.

### 5.5 Testing

- `pytest`. Unit tests for every pure function.
- Network is mocked in unit tests. Nothing in CI touches a real API.
- At least one integration test per external service, marked
  `@pytest.mark.integration`, skipped unless credentials are present.
- Test the failure paths, not just the happy one: missing photo, unknown agent,
  malformed row, expired token, Sheet unreachable.

---

## 6. Repository layout

```
gable/
├── CLAUDE.md                    # this file
├── ARCHITECTURE.md              # system design, data model, decisions
├── AGENTS.md                    # runtime agent behavior and Slack contract
├── README.md                    # setup and operations
├── .env.example                 # every variable, documented, no real values
├── .gitignore                   # .env, *.json keys, __pycache__, .venv
├── pyproject.toml
├── Makefile                     # deploy, lint, test, run
├── slack/
│   └── manifest.yml             # paste into api.slack.com
├── assets/
│   └── gable-icon-512.png       # Slack app icon
├── deploy/                      # systemd unit + droplet provisioning steps
├── spikes/                      # findings only; the spike tooling is deleted
├── tools/
│   └── check_connections.py     # prove every .env credential works, live
├── src/gable/
│   ├── config.py                # frozen settings dataclass, env parsing
│   ├── logging_setup.py         # structured logging + secret redaction
│   ├── models.py                # Listing, AgentProfile, RunRecord, PhotoResult
│   ├── sheets/
│   │   ├── client.py            # Google Sheets API wrapper
│   │   └── repository.py        # tab reads/writes, idempotency
│   ├── listings/
│   │   ├── normalize.py         # raw row -> Listing, validation
│   │   └── verify.py            # Firecrawl agent-detail verification
│   ├── photos/
│   │   ├── resolver.py          # the cascade, policy enforcement
│   │   ├── sources.py           # form / drive / web source adapters
│   │   ├── enhance.py           # reprocessing of a REAL photo only
│   │   └── store.py             # publish to a public HTTPS URL
│   ├── slides/
│   │   └── renderer.py          # pure Slides batchUpdate request builder
│   ├── slackapp/
│   │   ├── app.py               # Socket Mode bootstrap
│   │   ├── blocks.py            # Block Kit builders
│   │   └── handlers.py          # commands, actions, mentions
│   ├── pipeline/
│   │   └── orchestrator.py      # end-to-end run for one listing
│   └── cli.py                   # local invocation without Slack
└── tests/
```

Nothing in `src/gable/` imports from `slackapp/` except `slackapp/` itself. The
pipeline must be runnable from `cli.py` with Slack entirely absent — this is how
you develop and test without a live workspace.

---

## 7. Build order

Do not build these in parallel. Each gate must pass before the next begins.

### Phase 0 — Spikes (before any application code)

- **Spike A (blocking):** Determine whether an uploaded CSV/XLSX can carry image
  URLs into a Bulk Create image column. Ask Chase to run it in his account if you
  cannot. **If this fails, stop and report — do not design around it silently.**
- **Spike B:** Confirm the Google service account can read the Sheet.
- **Spike C:** Confirm Socket Mode connects and the bot can post to
  `C0BP597644B`.

### Phase 1 — Python pipeline (the whole product, minus one click)

Sheet watcher → normalize → verify → photo resolve → template lookup →
Bulk Create file → post to Slack with the file attached. Carmen downloads it and
runs Bulk Create in Canva.

Ship this. It captures nearly all the time savings.

### Phase 2 — Canva data-connector app (TypeScript, separate repo)

An Apps SDK data-connector app so Carmen picks listings inside Canva and never
downloads a file. **Gated on unknown #4 in section 4.3.** Do not start Phase 2
until Phase 1 has run successfully on real listings for at least a week.

---

## 8. The photo policy

**Chase has not decided this yet.** He leans toward allowing free AI generation.
Do not hardcode either answer. It is a single configuration value.

```
GABLE_PHOTO_POLICY=retrieve_only | generate_with_approval | generate_freely | no_ai
```

Default in `.env.example`: `generate_with_approval`.

The cascade, in order, always:

1. Photo attached to the form submission
2. Designated Google Drive folder, matched by address or listing ID
3. The listing agent's own brokerage site (best rights position)
4. Broader web search
5. Ask Carmen in Slack
6. Generate — **only if policy permits**

### Why the default is what it is

An image model cannot know what a specific address looks like. Given "123 Main
St," it invents a house. On a listing flyer that is not a stylistic choice — it
is a factually wrong photograph of a specific for-sale property, and a buyer can
drive to that address and find a different building. The real photo almost always
exists publicly, which makes this a retrieval problem, not a generation problem.

There is also a rights dimension: listing photos are typically the photographer's
copyright, and scraping Zillow violates their terms of service. Pulling from the
agent's own brokerage site is materially safer ground.

**Implement all four policies faithfully.** If Chase sets `generate_freely`, honor
it — but the generated image must always be tagged `ai_disclosure:
app_generated`, watermarked in the Slack preview as AI-generated, and logged as
such in the `Runs` tab. Never let a synthetic photo reach a flyer without leaving
a trace that says so.

Enhancement of a **real retrieved** photo (exposure, straightening, upscaling,
sky replacement) is permitted under every policy except `no_ai`. Enhancement and
generation are different operations and must be separate code paths.

---

## 9. Runtime environment

- **DigitalOcean droplet — verified live 2026-08-10.** The droplet `gable` exists:
  Ubuntu 24.04 LTS, SFO3, 1 vCPU / **1 GB** ($6/mo), at the address in the
  `Makefile`. The original plan said the $4 / 512MB tier; the machine that was
  actually built is the 1GB one. Size against 1 GB, not 512MB.
- 1 GB is still tight for Python plus image handling. The **1GB swap file is
  provisioned and active** (`/swapfile`, confirmed). **Stream images to disk —
  never load a full image into memory.** If you find yourself needing more, say
  so rather than quietly bloating the process.
- **Python 3.12.3** on the droplet (confirmed). `mypy` is nonetheless pinned to
  `python_version = "3.11"` as a deliberate floor — see the `ARCHITECTURE.md`
  decision log. `systemd` service, not `nohup`. Restart on failure.
- Deploy by git pull plus `systemctl restart`, driven by the `Makefile`. No
  editing files on the server by hand.
- Logs to `journald`, structured JSON, secrets redacted.
- **Socket Mode, not HTTP events.** No inbound ports, no TLS certificate, no
  domain. The tradeoff: without a public endpoint you cannot receive a Google
  Apps Script webhook, so the Sheet is **polled** on `GABLE_POLL_INTERVAL_SECONDS`
  (default 180). At this volume polling is correct and far simpler. Document that
  tradeoff in `ARCHITECTURE.md`.

---

## 10. Definition of done

A task is complete only when all of these are true:

- [ ] `mypy --strict` passes.
- [ ] `ruff` passes.
- [ ] `pytest` passes, and you watched it pass.
- [ ] No file exceeds 800 lines.
- [ ] Every function is annotated and has a docstring.
- [ ] Every assumption carries an `# ASSUMPTION:` comment.
- [ ] Failure paths are tested, not just the happy path.
- [ ] No secret appears in any log, commit, or docstring.
- [ ] `.env.example` covers every variable the code reads.
- [ ] **You did the §2.7 documentation sweep** — every doc your change made
      false is now true, in this commit, and the decision log has a row if a
      design decision moved.
- [ ] Your summary distinguishes what you verified from what you assumed, and
      names which documents you updated.

---

## 11. Things you must not do

- Enter any credential anywhere.
- Commit `.env` or a service-account JSON.
- Delete or overwrite rows in the Google Sheet. Gable **appends** to `Runs` and
  reads everything else. It never mutates form responses.
- Send a Slack message to any channel other than `C0BP597644B` without asking.
- Send email, or message a real-estate agent directly. Gable talks to Carmen and
  to Chase. It does not talk to clients.
- Publish, share, or export a Canva design. Gable prepares; Carmen decides.
- Push to `main` without Chase's review.
- Report a phase complete while any Section 4.3 unknown it depends on is still
  unknown.

---

## 12. When you are stuck

Say so plainly. Include: what you tried, what happened, what you think is wrong,
what you would try next, and what you need from Chase. A clear blocker report is
worth far more than a confident workaround built on a guess.
