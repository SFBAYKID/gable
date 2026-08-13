# Gable

Gable turns one Google Form submission plus one human-supplied property photo
into an editable Google Slides flyer. It works in Slack with Carmen and Chase,
never publishes outside the configured Gable channel, and never calls a flyer
ready unless its deterministic checks and rendered-image inspection both pass.

The current implementation and its limits are documented in
`AUDIT_2026-08-12.md`. Runtime language and safety rules live in `AGENTS.md`;
engineering constraints and the decision history live in `CLAUDE.md` and
`ARCHITECTURE.md`.

## What happens on a listing

1. The poller reads the form-response tab by header name and records the row in
   SQLite without modifying the Sheet.
2. Gable selects the native Google Slides file in `Generic Templates` whose
   name matches the form's request type.
3. Before copying anything, it reads the current source file, resolves the
   fields, measures the text boxes and hero frame, and checks the listing's real
   values and supplied photo against those measurements.
4. A structural problem or unreadable result pauses the listing. A measured
   but usable text or crop warning asks Carmen whether to run as-is or update
   the source template.
5. A Slack photo keeps its original composition until the exact frame is known.
   Gable then crops and resizes once. Enlargement up to 2x is local; beyond 2x,
   one policy- and spend-gated GPT Image 2 edit may restore resolution while a
   fidelity check preserves the property and composition.
6. Gable copies the template, fills standalone fields, reads every value back,
   places the hero and headshot, fits only text it changed, renders a thumbnail,
   and asks the configured vision model to compare the supplied property photo
   with the visible result.
7. Only a confident pass is linked as ready. The output is a live Slides file,
   so Carmen can also correct it directly.

When a cycle attempts at least two listings, its channel summary counts only
delivered files as ready and separates paused, failed, and skipped requests.

Every user-triggered Slack turn, including template rechecks and photo uploads,
uses the same native purple waiting state and switches to the real work stage
after the short personality sequence.

New source templates receive deterministic structure and text-capacity checks,
then a placeholder-aware visual inspection for clipping, overlap, spacing,
alignment, padding, and off-canvas artwork before they are certified.

## Template contract

- Put templates in `Templates / Generic Templates` as native Google Slides.
- Name each file exactly like the corresponding form request type. Matching is
  case- and whitespace-tolerant; duplicate names are refused.
- Use exactly one slide.
- Keep every replaceable value in its own ordinary text box. Supported labels
  and sample-value conventions are resolved by `slides/fields.py`.
- Keep one separate, unfilled main-photo shape near the top of the slide. Gable
  refuses to infer a frame when more than one candidate is plausible.
- Give normal addresses up to 52 average characters, emails up to 42, and agent
  names up to 28 enough room at the intended type size. These are certification
  targets; each listing is measured again with its actual content.

The first template-folder scan adopts existing files silently. Every later new
file is measured once and gets its own Gable-owned Slack thread. After changing
the source, reply in that thread that it is updated; Gable reloads the same Drive
file and checks it again. A listing-specific warning also accepts an explicit
instruction to run the current design anyway, but structural and unreadable
problems are never overridable.

Two-agent roles can be read from the form notes, but role-specific text and
portrait objects are not certified yet. Those requests pause before copying a
template rather than inferring placement from page order.

## Slack operations

Only the two stable user IDs in `GABLE_SLACK_ALLOWED_USER_IDS` can mention,
reply to, upload to, or operate Gable. The `/gable` command supports:

- `status` — latest-listing pending, ready, and failed counts plus poll state;
- `run` — refresh sources and recheck current paused listings in the same run;
- `retry run ID` — queue one fresh bounded attempt for the latest run;
- `templates` — list current native Slides files in `Generic Templates`;
- `pause` and `resume` — control scheduled polling without disabling threads.

Slash replies are ephemeral. A queued paused-listing recheck uses the ordinary
native purple waiting state in that listing's owned thread.

## Setup

Requirements are Python 3.11 or newer, a Slack app installed in Socket Mode, a
Google service account with Sheets, Drive and Slides enabled, the intake Sheet,
and a Google shared drive. Share only those two resources with the service
account. Source templates and output must be in the shared drive because a
service account has no usable My Drive storage quota.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/python tools/check_connections.py
.venv/bin/python tools/adopt_backfill.py
make check
python -m gable.slackapp.runtime
```

Fill `.env` before running connection checks. Never commit it or place the
service-account JSON inside the repository.

Apply [slack/manifest.json](slack/manifest.json) and reinstall the Slack app when
the command or scope set changes. Production startup requires the three Drive
locations, the OpenAI key used by the fail-closed visual gate, and exactly two
allowed Slack user IDs; legacy Slack OAuth client variables are rejected.

## Verification

`make check` is the definition-of-done gate: Ruff formatting and lint, strict
Mypy, and the full Pytest suite.

To exercise the real new-template path without touching the response Sheet or
posting to Slack, copy and inspect one existing source through a temporary
SQLite database:

```bash
.venv/bin/python tools/template_smoke_test.py --source Sold
```

The tool creates one recoverable Drive copy, runs the production triage logic,
prints the result locally, and moves only that temporary copy to Drive trash in
a `finally` block.

## Important limits

- Perfect output cannot be guaranteed by any current vision model. Gable's
  contract is stronger and testable: prevent deterministic defects before a
  copy, fail closed when visual review is unavailable or uncertain, and leave
  an editable Slides file for the final human decision.
- Hero-photo web search, Drive-photo selection, MLS access, and synthetic
  property-photo generation are not connected. The runtime uses the one photo
  supplied in the listing's Slack thread.
- The append-only run-event ledger has no operator history viewer yet.
- Automatic focal-point crop retries and second-model visual consensus are not
  connected; both need measured reliability evidence before becoming gates.
- The source-template capacity check estimates average glyph width because the
  Slides API does not expose final line-break layout. Actual values are measured
  again, text is read back after mutation, and the rendered image is the final
  gate.
- A hard cumulative $50 ledger guards connected paid calls. A listing gets at
  most one image-edit attempt and three fresh run attempts.
