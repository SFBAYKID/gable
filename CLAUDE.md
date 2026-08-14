# CLAUDE.md — Gable

Operating instructions for the coding agent building **Gable**, a Slack-native
agent that turns real-estate listing form submissions into finished Google Slides
posts.

Read this file completely before writing any code. Read `ARCHITECTURE.md` next.
`AGENTS.md` describes the runtime agent's behavior; this file describes *your*
behavior while building it.

---

## 1. What Gable is

Carmen is a designer. Real-estate agents submit listings through a Google Form.
Each submission becomes a social post she builds by hand — roughly 20 minutes
each, much of it spent retyping fields and fitting the supplied property photo.

Gable removes that work. It watches the Google Sheet behind the form, identifies
the submitting agent, picks their template, asks Carmen in Slack for the hero
photo, fits that photo to the template frame, renders a finished **Google Slides**
file, and posts her a link. She opens it, adjusts anything she wants, or replies
in the thread and Gable redoes it. Her 20 minutes becomes about one.

**Gable does not replace Carmen's judgment. It removes her typing and routine
photo fitting.** Every post still passes through her before it reaches a client.

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
- **Commit and push to `main`, once the §10 gates actually pass.** Review moved
  to GitHub, where Chase can read a diff properly. The gates are the price of
  this: pushing red is worse than not pushing, because it puts work nobody
  verified in front of the next agent as though it were finished.

**Always ask. These are Chase's calls, not yours:**

- Anything requiring a credential, or any step that would have you enter one.
- Spending money that isn't already budgeted, or raising a rate ceiling.
- Anything irreversible: deleting data, mutating the form-responses tab,
  force-pushing, destroying a droplet.
- A finding that resolves *against* the design, the way Spike A did. Stop and
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
already cost this project twice — `ARCHITECTURE.md` described a Canva Bulk
Create export for days after the code had moved to Google Slides, and §1 of this
very file still promised "a Canva Bulk Create payload" long after Spike A had
killed that path.

So: **before you report any work complete, re-read the docs your change touched
and update the ones your change made false.** In the same commit, not later.

| If you changed… | Update, in the same commit |
|---|---|
| A design decision, or a tradeoff | `ARCHITECTURE.md` — the affected section **and** a new row in `DECISIONS.md` |
| A module, a package, or a file's home | The `CLAUDE.md` §6 layout tree |
| Anything the runtime agent says or does | `AGENTS.md` |
| A variable the code reads | `.env.example`, with the comment explaining it |
| Setup, deploy, or operations | `README.md` |
| What is blocked, decided, or waiting on Chase | `STATUS.md` |
| Something you verified live that the design rests on | §4.3 of this file, with the evidence |

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

Chase has offered logins for DigitalOcean, Slack, and Google.

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

## 4. Why this renders in Google Slides

Canva was the original target. It is gone, and the reasoning is kept because it
is the reason nobody repeats the week that proved it.

### 4.1 Spike A — settled, and it settled against the design

**An uploaded CSV or XLSX types every column as text.** Only Canva's *manual*
data-entry table can carry an image-typed column. Proven by running both paths
against the same design in the same session: the manual path produced a column
with an image glyph, the upload produced a `T` on all four fields including the
photo URL.

The photo is the twenty minutes. A file that can carry every text field but not
the photo does not solve the problem. Full writeup in `spikes/SPIKE_A_RESULT.md`.

Two further Canva paths were closed at the same time:

- **Connect API autofill** needs a Canva **Enterprise** organization. The account
  is on Teams. Chase ruled out the cost.
- **Bulk Create has no API at all**, so even the working path still required a
  human at a keyboard — which is the thing being removed.

### 4.2 What replaced it

Google Slides, on infrastructure already required for the Sheet. Safe
`replaceAllText` requests fill standalone fields. The measured photo frame is
deleted and a fitted `createImage` replacement is created at the same exact
size and transform from a public URL. No Enterprise plan, marketplace review,
or second language is required; the same service account handles it.

Design detail lives in `ARCHITECTURE.md` §2.1 and `DECISIONS.md`. Do not
re-open Canva without reading both.

### 4.3 Verified evidence, so build on these

Items 1–6 were established against the real APIs. Item 7 combines Slack's
current vendor contract with Chase's report of the previously visible behavior.
Item 8 was refreshed against first-party model documentation and a live model
call on 2026-08-12:

1. **Slides accepts a plain `http://` image URL.** It rejects every Google Drive
   URL form, even when the file is world-readable and serves valid bytes to an
   anonymous client. Slack cannot host either — `files.sharedPublicURL` needs a
   user token, not a bot token. The droplet serves photos over nginx.
2. **A service account has a 0 GB quota**, so everything lives in a shared drive.
   Contributor is sufficient; it can trash but not purge, and Gable never purges.
3. **Slides has no text-inset field.** "Padding" cannot be set through the API.
4. **A divider rule is a Line, not a Shape**, and needs `updateLineProperties`.
5. **`applyMode: RELATIVE` multiplies translation as well as scale**, so every
   resize sends ABSOLUTE and preserves position explicitly.
6. **`action_id` must be unique within a Slack message**, or the whole message is
   rejected.
7. **`assistant.threads.setStatus` is Gable's native purple waiting surface.**
   Slack's current method reference and March 2026 scope update explicitly allow
   channel-based apps to use it with `chat:write`, and setting it on a new user
   message opens the reply thread. A single live call returned `ok` without a
   visible result on 2026-08-12 and led to its mistaken removal; Chase identified
   that removal as a regression from the visibly working purple treatment.
   The restored timed sequence still needs a watched live confirmation after
   deployment. Vendor contract:
   https://docs.slack.dev/reference/methods/assistant.threads.setStatus/
8. **Exact Slides geometry plus a frontier visual judge is the strongest
   connected measurement stack.** Google requires rendered size to combine an
   element's size and affine transform, including parent groups. OpenAI's model
   catalogue names GPT-5.6 Sol as its flagship and says current models support
   image input; the configured account completed a strict Responses vision
   request with `gpt-5.6-sol`. Property-photo fitting is now deterministic and
   source-only; it does not call an image model. Sources:
   https://developers.google.com/workspace/slides/api/concepts/transforms
   https://developers.openai.com/api/docs/models

Each of these cost a real failure to learn. They are in the decision log with
their evidence.

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
├── ARCHITECTURE.md              # system design and data model
├── DECISIONS.md                 # append-only decision log, with reasons
├── AGENTS.md                    # runtime agent behavior and Slack contract
├── README.md                    # setup and operations
├── TESTING.md                   # repeatable unit, integration and live checks
├── .env.example                 # every variable, documented, no real values
├── .gitignore                   # .env, *.json keys, __pycache__, .venv
├── pyproject.toml
├── Makefile                     # deploy, lint, test, run
├── slack/
│   └── manifest.json            # paste into api.slack.com
├── assets/
│   └── gable-icon-512.png       # Slack app icon
├── deploy/                      # systemd unit + droplet provisioning steps
├── spikes/                      # findings only; the spike tooling is deleted
├── tools/
│   ├── check_connections.py     # prove every .env credential works, live
│   ├── adopt_backfill.py        # mark existing rows as history, build none
│   ├── adopt_rows.py            # assert and adopt named historical rows only
│   ├── preview_poll.py           # read-only preview of work polling would open
│   ├── reconcile_image_reservation.py # evidence-gated pre-inference release
│   ├── run_row.py               # start one row by tab and number, or resume it
│   └── template_smoke_test.py   # recoverable live new-template path
├── src/gable/
│   ├── config.py                # frozen settings dataclass, env parsing
│   ├── logging_setup.py         # structured logging + secret redaction
│   ├── agents/
│   │   ├── contacts.py          # read-only roster workbook mirrored atomically
│   │   └── website.py           # official-domain fallback for workbook blanks
│   ├── sheets/
│   │   ├── client.py            # Google Sheets API wrapper
│   │   ├── identity.py          # whole-tab identity and source-row aliases
│   │   └── repository.py        # tab reads/writes, idempotency
│   ├── db/
│   │   ├── schema.py            # tables and migrations (SQLite)
│   │   ├── store.py             # submissions, facts, roster, spend + reexports
│   │   ├── question_store.py    # durable Slack question and outcome outbox
│   │   ├── run_store.py         # attempts, states, latest counts, event writes
│   │   └── template_store.py    # source catalogue and triage verdicts
│   ├── listings/
│   │   ├── intake.py            # the eleven columns that matter, found by header
│   │   ├── headers.py           # how a header is compared, never a position
│   │   ├── address.py           # casing and punctuation, nothing invented
│   │   ├── enrich.py            # look up beds, baths, square footage
│   │   └── review.py            # a client review, read out of what was typed
│   ├── photos/
│   │   ├── fit.py               # exact crop plus safe small-source composition
│   │   ├── headshots.py         # the agent's face, from Drive, republished
│   │   └── store.py             # publish to the droplet over http
│   ├── slides/
│   │   ├── edits.py             # one tool per change Carmen can ask for
│   │   ├── geometry.py          # move, resize, delete — the transform traps
│   │   ├── elements.py          # recurse through imported element groups
│   │   ├── selection.py         # the design named for the form's request type
│   │   ├── fields.py            # what a design's text means
│   │   ├── fitting.py           # shrink text that does not fit its box
│   │   ├── manifest.py          # what a design needs before it renders
│   │   ├── hero.py              # measure the photo and headshot frames
│   │   ├── preflight.py         # source structure, exact fit and crop checks
│   │   ├── replacement.py       # substring-safe fills and reply proof
│   │   ├── library.py           # current Generic Templates contents
│   │   └── edit_common.py       # shared colours, guards, request type
│   ├── slackapp/
│   │   ├── app.py               # Socket Mode listener
│   │   ├── batches.py           # ready-only multi-listing summary
│   │   ├── brain.py             # reads intent, picks a tool, asks when unsure
│   │   ├── context.py           # bounded owned-thread turns + listing facts
│   │   ├── editing.py           # execute edits on the thread's Slides file
│   │   ├── outbox.py            # conservative proof of lost Slack acknowledgements
│   │   ├── photos.py            # Slack upload to fitted same-run resume
│   │   ├── recovery.py          # interrupted runs and accepted-but-unfinished uploads
│   │   ├── routing.py           # keep ordinary replies inside Gable-owned threads
│   │   ├── runtime.py           # production Slack + poller assembly
│   │   ├── source_refresh.py    # re-read the exact sources behind a paused run
│   │   ├── status.py            # a working indicator that cannot break the work
│   │   └── style.py             # the house style, enforced
│   ├── pipeline/
│   │   ├── contact_gate.py      # pre-Slack agent values + one cached site lookup
│   │   ├── needs.py             # one batched ask, and the structural stops' wording
│   │   ├── questions.py         # persist, post, confirm and retry run notices
│   │   ├── runner.py            # one complete listing, every exit recorded
│   │   ├── live.py              # concrete Google, photo, research and vision seams
│   │   ├── schedule.py          # when to poll: busy hours vs quiet
│   │   ├── poller.py            # watch loop and historical backfill guard
│   │   ├── template_triage.py   # proactive source audit and recheck
│   │   ├── template_vision.py   # spend-gated source render inspection
│   │   ├── vision.py            # strict source-versus-render visual verdict
│   │   └── orchestrator.py      # pure listing decisions
│   ├── runtime.py               # Slack-free process lifecycle
│   └── cli.py                   # local invocation without Slack
└── tests/
```

Nothing in `src/gable/` imports from `slackapp/` except `slackapp/` itself. The
pipeline must be runnable from `cli.py` with Slack entirely absent — this is how
you develop and test without a live workspace.

---

## 7. Build order

Do not build these in parallel. Each gate must pass before the next begins.

### Phase 0 — done

Spike A answered (against Canva), Google access proven, Socket Mode connected,
the droplet provisioned and serving photos.

### Phase 1 — the pipeline, end to end

Sheet watcher → normalize → identify the agent → select the source → research
only public fields that source displays → ask what is not → render a Slides
copy → check it twice → post the link.

**Built, joined, and tested:** the runner drives the orchestrator, property
research, current-template preflight, supplied-photo handoff, copy/fill/readback,
photo and headshot placement, text fitting, strict rendered inspection, Slack
delivery, and same-thread edits. The poller invokes that runner for new rows.
Natural-language replies in a Gable-owned thread refresh the current Sheet,
roster, and source template before continuing the same paused run; there is no
operator command queue or Slack service-control surface.

**Before live polling is enabled**, preview it against the deployed database.
The current audit found three exact pre-release rows not recorded as handled;
Chase must confirm them, `tools/adopt_rows.py` must adopt only their asserted row
and content hashes, and `tools/preview_poll.py --expect-none` must then prove no
historical work would open. `adopt_backfill.py` remains only for bootstrapping a
genuinely fresh database from an entirely historical source snapshot.

### Phase 2 — after Phase 1 has run for a week on real listings

Reconcile the three placeholder conventions across the 45 templates, then
consider the dual-agent open-house designs. Neither is code work first; both are
decisions about what Carmen wants.

## 8. The photo policy

The connected source is one real property photo Carmen or Chase supplies in the
owned Slack listing thread. The default and production setting is
`retrieve_only`; despite its compatibility name it does not perform automatic
retrieval. `no_ai` is equivalent for the connected source-only fitting path.

```
GABLE_PHOTO_POLICY=retrieve_only | no_ai
```

Default in `.env.example`: `retrieve_only`.

### Why the default is what it is

An image model cannot know what a specific address looks like. Given "123 Main
St," it invents a house. On a listing flyer that is not a stylistic choice — it
is a factually wrong photograph of a specific for-sale property, and a buyer can
drive to that address and find a different building. That is why the supplied
human photo remains the source of truth.

The legacy enum still parses `generate_with_approval` and `generate_freely` so
old configuration produces a precise startup error rather than an unknown-value
error. Runtime rejects both: there is no synthetic generator, approval action,
or disclosure workflow. If generation is ever connected, it must carry all
three `AGENTS.md` disclosure mechanisms or fail closed.

Every fit is local. Beyond 2x, the foreground stays at the safe ceiling over a
blurred and darkened fill derived only from the same upload. Neither policy
changes the property, replaces a sky, or invents missing content.

---

## 9. Runtime environment

- **DigitalOcean droplet — verified live 2026-08-10.** The droplet `gable` exists:
  Ubuntu 24.04 LTS, SFO3, 1 vCPU / **1 GB** ($6/mo), at the address in the
  `Makefile`. The original plan said the $4 / 512MB tier; the machine that was
  actually built is the 1GB one. Size against 1 GB, not 512MB.
- 1 GB is still tight for Python plus image handling. The **1GB swap file is
  provisioned and active** (`/swapfile`, confirmed). Slack downloads are capped
  at 25 MB before Pillow opens them; resized derivatives and model payloads are
  held in memory only within that bound. Do not add unbounded reads or retain
  images between runs.
- **Python 3.12.3** on the droplet (confirmed). `mypy` is nonetheless pinned to
  `python_version = "3.11"` as a deliberate floor — see `DECISIONS.md`. `systemd` service, not `nohup`. Restart on failure.
- Deploy by git pull plus `systemctl restart`, driven by the `Makefile`. No
  editing files on the server by hand.
- Logs to `journald`, structured JSON, secrets redacted.
- **Socket Mode, not HTTP events.** No inbound ports, no TLS certificate, no
  domain. The tradeoff: without a public endpoint you cannot receive a Google
  Apps Script webhook, so the Sheet is **polled** on `GABLE_POLL_INTERVAL_SECONDS`
  (default 600 outside business hours; 120 during them). At this volume polling
  is correct and far simpler. Document that
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
- Delete or overwrite rows in the Google Sheet. Gable reads the response tab
  only; submissions, run states, append-only transitions, template audits, and
  spend live in SQLite.
- **Post anything to `C0BP597644B` while testing.** That is **#calvo, the
  production channel**, where Carmen and real staff are. All testing goes to
  **`C0B02721MNK` (monarch-bot-playground)** and nowhere else.

  This line previously read "never send a Slack message to any channel other
  than `C0BP597644B`", which named the production channel as the only approved
  one and contradicted `AGENTS.md` §2. An agent followed it and posted a test
  photo into #calvo on 2026-08-11. Check the channel id against this line and
  `AGENTS.md` before any Slack write, and never hardcode one from memory.
- Send a Slack message to any channel other than those two without asking.
- Send email, or message a real-estate agent directly. Gable talks to Carmen and
  to Chase. It does not talk to clients.
- Publish, share, or export a finished design. Gable renders a Slides file into
  the Gable drive and links Carmen to it. She decides what leaves the building.
- **Force-push, rewrite history, or push anything that fails the §10 gates.**
  Pushing to `main` is yours to do — Chase reviews on GitHub rather than
  blocking the commit. Rewriting published history is not, ever: it is the one
  git operation that destroys someone else's work silently. `git push` yes,
  `git push --force` no.
- Report a phase complete while any Section 4.3 unknown it depends on is still
  unknown.

---

## 12. When you are stuck

Say so plainly. Include: what you tried, what happened, what you think is wrong,
what you would try next, and what you need from Chase. A clear blocker report is
worth far more than a confident workaround built on a guess.
