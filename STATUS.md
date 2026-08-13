# Gable — status, and what's needed from Chase

Last updated 2026-08-13 by the building agent.

## 2026-08-13 the release candidate's gate is green

`ruff format`, `ruff check`, `mypy --strict` and 1,269 tests all pass locally.
Three failures the previous handoff recorded are fixed: a stale photo question
could be posted while the answering upload was still downloading, a test still
imported two names from `slackapp.runtime` after they moved to
`slackapp.source_refresh`, and two files were unformatted.

The photo race is closed properly rather than papered over. The handoff writes a
durable `file_share` claim before refreshing sources, downloading and
publishing, and releases it only once an outcome is stored; retiring the
question early is safe only because of that claim. `slackapp/recovery.py` reads
incomplete claims on every notification pass and either releases one whose run
already recorded the upload or asks for the image once more. Two mutation checks
confirmed the new tests fail when either half is removed.

`slackapp/runtime.py` was at the 800-line ceiling, so the startup-recovery
helpers moved into `slackapp/recovery.py` (runtime is now 700). The photo test
fixtures moved into `tests/photo_support.py` for the same reason.

**Nothing is deployed and polling is still off.** Blockers 2, 3 and 4 below are
open; none of them fire during the Mike photo-upload resume, which is why that
acceptance test can proceed first, but each must be closed or explicitly gated
before the behaviour it guards is switched on.

**`ARCHITECTURE.md` is now exactly 800 lines — the hard ceiling.** The next
decision-log row requires splitting it first.

## 2026-08-13 release audit after the pixelated Mike run

The current release candidate is not deployed. The playground service remains
on `c4c6f16`, active with `GABLE_POLL_ENABLED=false`; its database is schema 7.
Mike's existing run `run-e86567a4fa95` remains `needs_photo` in its original
thread, waiting for one correct replacement property image. The rejected draft
is retained for audit, but its link is not a delivery.

The release candidate makes every question, final link, review explanation,
failure, and interruption notice a durable Slack outbox item. Its retry loop is
independent of Sheet polling, and an answering owned-thread upload satisfies a
pending photo request before file preparation begins. A lost acknowledgement is
confirmed only by one exact Gable-authored match in bounded Slack history. It
handles small uploads with source-only fitting and fixes form identity for
duplicate timestamps and movable rows through whole-tab reconciliation and a
source-row ledger.
Headshot discovery now recognises both an empty Slides shape and an existing
portrait image only when that image shares a measured card with a resolved agent
name and phone, email or title. Logos, QR codes and secondary photos do not
qualify on shape alone; multiple candidates stop. A read-only walk of all 74
pages in the static source found three image objects, all icon-sized and none
misclassified. The live Sold source still has exactly one portrait shape,
`p1_i20`, and no competing candidate; replacement restores its layer boundary.

Automatic polling is not ready to enable blindly. A read-only preview against a
fresh dump of the live database reads all 105 production responses and finds
seven unhandled historical rows. Four are distinct answers that the deployed
timestamp/email/address key had collapsed into the preceding row: 13
(`3a86888ecab252cf`), 72 (`bbf229dd34a9154b`), 94
(`040dde7acebf55fd`) and 103 (`db31f2914f8fbbfe`). The other three are row 46
(`1d63ec043ba9ccdf`), row 105 (`a233bc6d3ad3ed38`) and row 106
(`f78a5d67b4cda3cc`). A temporary clone proved this set is deterministic, exact
adoption marks all seven skipped without repointing any existing run, and two
subsequent previews both return zero; production was not changed. Chase must
confirm they are historical. After deploy, `tools.adopt_rows` can assert the
seven exact row/hash pairs without Slack or flyer work, and
`tools.preview_poll --expect-none` must return zero before polling is enabled.
The live template catalogue is also empty because scanning has never run; the
first enabled scan safely baselines current files before the same poll can start
listing work.

## 2026-08-13: natural-language Slack and current release gate

The current review is in `AUDIT_2026-08-13.md`; the exact verification sequence
is in `TESTING.md`. The `/gable` declaration, `commands` OAuth scope, Bolt
handler, command service, operator queue, poll pause/resume controls, and their
command-only database queries are removed. The saved Slack app manifest was
updated successfully and its Slash Commands page now has no configured command.

Owned-thread natural language remains. Chase's exact sentence, “Hey, can you
rerun this project?”, deterministically reloads the current source and resumes a
paused run. The full gate caught and fixed the greeting-normalization mismatch.
The review also fixed a fail-open edge case where a confident negative visual
verdict with no explanatory items could otherwise leave the problem list empty
and deliver.

Commit `c4c6f16` is deployed in monarch-bot-playground and the service is active.
The controlled release target remains Testing_1 line 48: Mike Kulnich, Sold,
703 Perception Way, Aberdeen, MD 21001. The same owned-thread run retains Mike's
exact contact record, filed headshot, current Sold source, and supplied photo.

Property-photo fitting is now entirely local and deterministic. No image
generator or enhancement provider is connected. A source needing more than 2x
enlargement keeps its complete foreground at no more than 2x over a blurred,
darkened fill derived only from the same upload. Rejected draft links stay out
of Slack, and a confidently proved source-photo contradiction returns the same
run to `needs_photo` for one replacement upload.

The current repository also composites transparent uploads onto a deterministic
white matte, serializes memory-heavy image work, downsamples large headshots
before fitting, and rejects unsafe target dimensions before decoding. The
275×183 Mike upload can be fitted honestly, but its visible source detail cannot
be recreated. The release gate is therefore not successful until a supplied
photo produces a delivered flyer that Chase opens and confirms visually. Prior
provider experiments and credential exposure remain in the chronological record
below; they are not descriptions of the current fitting path.

## 2026-08-12 current quality gate

The six-part quality and triage audit is complete; current findings are in
`AUDIT_2026-08-12.md`. Source geometry preflight runs before any copy, new and
revised templates receive deterministic and placeholder-aware visual triage,
and an update in the same Slack thread reloads current Drive bytes under the
native waiting state. Listing clearance is revision-specific, template notices
survive Slack delivery failure without another paid inspection, and deleting a
source revokes its old ready state.

Slack photos retain their composition until the exact hero frame is measured,
hero and headshot replacements preserve the source frame's layer boundary, and
the final `gpt-5.6-sol` inspection compares the human photo with Google's
render. Conversation tool routing also uses Sol through the Responses API; a
live test caught and fixed the former Chat Completions failure, then proved that
“Update the image” produces a hero-or-headshot clarification.

The recoverable Drive smoke test copied `Sold`, detected roughly 34 address
characters of capacity against the 52-character certification target, recorded
`needs_template`, and verified the temporary copy in trash. No response-Sheet
write, Slack post, deployment, or finished-design publication occurred.

At that checkpoint, all 907 tests, Ruff format and lint, strict Mypy, Vulture,
dependency integrity, and diff checks passed. The Slack manifest still required
reinstallation then; the current manifest state is recorded in the audit above.
The OpenAI credential exposed during diagnostics must be rotated first; it is
not recorded in this repository or repeated here.

The sections below are chronological evidence and may describe an older state;
the dated audit and `ARCHITECTURE.md` are authoritative for current behavior.

**Handing off?** Read `TEMPLATE_CERTIFICATION.md` for what is actually proven
and which numbers can be trusted, `TEMPLATE_ISSUES.md` for the seven defects
that belong to Carmen, and `BRAND.md` for the fonts, colours and the fact that
not every design in the deck is a listing flyer.

## 2026-08-12 evening: delivered, from a sheet row to a finished flyer

Row 62 of `Form Responses 1` — Andy Jang, Sold, 300 Commerce St # D, Havre de
Grace — went from a simulated submission to a **delivered** flyer in Slack, with
no stop for review: the listing announced in the channel, the photo requested in
its thread, Chase's upload fitted and published, the `Sold` design taken from
Generic Templates by name, Andy's own headshot published from the Head Shots
folder, his direct line read from the contact workbook, and one closing message
carrying the link.

Five defects were found and fixed by watching that run rather than by reasoning
about it:

1. **The photo was pillarboxed.** `createImage` fits rather than fills, and the
   upload was cropped to the slide's 4:5 canvas instead of the frame's 2.14:1
   band — a narrow column of photograph with the layout showing either side.
   This was true of every design whose photo area is not 4:5.
2. **A question nobody was waiting for.** A non-blocking advisory was posted
   mid-build, phrased as a question, and then walked past. Advisories are now
   statements folded into the one closing message.
3. **Four messages for one flyer.** Now one, carrying the link.
4. **The indicator kept pulsing under a question**, which reads as "still
   working" and stops anyone from answering.
5. **A combined announcement started no thread**, so an upload had nowhere valid
   to land — the handoff matches a photo to a run by its thread.

**The `Sold` design has no price field**, so a sale price cannot appear on it as
built. If Carmen wants sold prices shown, that template needs one added.

## 2026-08-12: a full run, end to end, and the four things it caught

`Testing_1` row 78 (Eric Jacobs, Sold, 23 Pierside Ave Unit 118, $330,000) was
run from the sheet to a finished flyer in the playground channel: Gable asked
for the photo by name and request type, Chase dropped one in the thread, and it
was enlarged, fitted, placed, and rendered onto a Just Sold design with Eric's
own headshot, phone and email. It stopped at `needs_review` with the link and a
specific note, which is the design working.

Getting there exposed four defects, all now fixed and deployed:

1. **Columns were read by position.** `Testing_1` splits the agent name across
   two columns, shifting everything from D rightward, so its row 78 read as the
   acknowledgment paragraph for a request type and "Instagram Story" for an
   address. Columns are found by header text now, and an unrecognisable tab is
   refused rather than guessed at.
2. **The roster sync had been storing nobody, silently, all day.** The
   `Sales_People` header moved to row 1 when the tab was rebuilt with 39 agents;
   the range still started at A2, so Andy Jang's details became the column names
   and every row was skipped for having no email. Any flyer built today carried
   the brokerage's main number and the design's own stock face.
3. **The template picker only ever looked for its top choice.** Just Sold has
   eleven eligible designs and reported having none, because the one ranked
   first is not imported. It now takes the best design that exists, unless the
   submission named the missing one by cue.
4. **Two address normalisers disagreed.** The flyer's check ran its own weaker
   copy that only recognised an upper-case state, so "Baltimore Md 21230" never
   gained its comma and a complete run stopped to ask for an address already
   supplied. There is one set of address rules now, and a unit stays with its
   street so condo addresses still name their city.

**The open item is data, not code: only 11 of the 45 designs are marked
`gable_role=template` in the drive.** Just Sold has exactly one, so the design
choice was forced rather than made. Importing the rest is what turns selection
from theoretical into real.

`tools/run_row.py` starts one row by tab and number, and `--resume` continues a
paused source, information, or review run using its retained supplied photo. It
refuses `needs_photo`; that state clears only when Carmen or Chase uploads the
replacement in the owned Slack thread. The poller only ever starts rows it has
never seen, so a row already on the sheet cannot be run by waiting.

## Where this actually stands, 2026-08-11 evening

**Photos: proven.** A person can drop in a photo of any reasonable size or shape
and it lands correctly. Nine source shapes from 275x183 to 6000x4000 — landscape
through panorama, PNG included — plus a multi-megabyte EXIF-rotated portrait
carried through the real Slack upload, download, publish and fit path. All land
at exactly 1080x1350.

**Flyers: not proven, and not provable without Carmen.** Of the 45 templates,
**2 produce a flyer verified clean** — correct address, no stray content, no
surviving placeholders. Two more reached "delivered" and were then refused once
the checks caught what they carried. The rest stop for review.

The remaining gate is a judgement — "a flyer Carmen would post without touching
it" — and no measurement substitutes for it. **Nothing here has been certified,
because certification means she looked.**

### What to trust when reading progress numbers

The deterministic checks are stable and moved cleanly across the session:

| | Start | Now |
|---|---|---|
| templates that can place a hero photo | 3 | 39 |
| templates with headshot detection | 0 | 32 |
| blocked by the substring guard | 13 | 2 |
| flyers verified clean | 0 | 2 |

The vision-pass categories — overlap, placeholder, clipped — are **not** stable.
Four walks of identical code against identical templates produced overlap counts
of 6, 11, 8 and 13. Use the vision pass to find a defect worth looking at on a
specific flyer; do not use it to measure whether a change helped.

### Three guards now stop things that previously shipped

Each exists because it reached a delivered flyer: a value that does not read back
as supplied, a phone number or email belonging to somebody not on the listing,
and sample content or a malformed price from the design itself. All three are
deterministic and live in `pipeline/audit.py`.

At that checkpoint, the automatic runtime was wired, deployed, active, and
watching the Sheet. `slackapp.runtime` constructs the real Google clients,
database, `Poller`, and `Runner`; Socket Mode connects in the background while
the poller runs on the main thread. Polling is disabled in the audited live
service described above. `cli.py` also runs one guarded pass locally without Slack.

The Slack photo handoff is built and unit-tested. A
`file_share` reply is matched to its thread's paused run, downloaded, fitted,
hosted, verified, and used to resume that same run. **The receive and download
portion is verified live:** at 10:24 on 2026-08-11 Gable fetched a real Slack
upload, read its dimensions, and reached the former undersized-photo refusal.
That proves `files:read` is installed. Commit `e09bb27` replaces that refusal
with the guarded automatic upscale and is deployed. A second watched upload
reached the image model successfully; the seam gate rejected the derivative and
the local fallback exposed a root-owned photo directory. The directory is now
owned by `gable:gable`, verified writable as the service user, and repaired on
every future deploy. The exact production database at `/opt/gable/var/gable.db`
has the historical backfill marker.

At 11:00 Pacific, the same Slack file was replayed without another image-model
call. It was resized, published, and attached to the original run. That replay
exposed and fixed two more live defects: an address missing only its city comma
was rejected, and a follow-up reply replaced the stored root thread timestamp.
The run now correctly remains in its original thread at `needs_info`, waiting
for Chase's agent phone number rather than guessing one.

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
are built. They are not yet enough to certify all 45 templates visually. Three
templates have explicit measured hero-layer ids; the other 42 refuse placement
instead of guessing. Headshot replacement is missing. Conversational font,
colour, correction, resize, move, and status tools now execute against the
thread's Slides file and report completion only after Google confirms the
batch. No flyer should be called demo-ready until a real uploaded-photo render
has been inspected. The certification ledger remains 0 of 45 approved.

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
