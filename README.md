# Gable

Gable turns one Google Form submission plus one human-supplied property photo
into an editable Google Slides flyer. It works in Slack with Carmen and Chase,
never publishes outside the configured Gable channel, and never calls a flyer
ready unless its deterministic checks and rendered-image inspection both pass.

The current implementation and release state are documented in `STATUS.md` and
`TESTING.md`. `AUDIT_2026-08-13.md` is the dated command-removal audit. Runtime
language and safety rules live in `AGENTS.md`; engineering constraints and the
decision history live in `CLAUDE.md` and `ARCHITECTURE.md`.

## What happens on a listing

1. The poller reads the form-response tab by header name and records the row in
   SQLite without modifying the Sheet.
2. Gable selects the native Google Slides file in `Generic Templates` whose
   name matches the form's request type.
3. Before copying anything, it reads the current source file, resolves the
   fields, measures the text boxes and hero frame, and checks the listing's real
   values and supplied photo against those measurements.
4. A structural problem or text that cannot remain readable pauses the listing.
   Readable overflow and photo cropping are corrected automatically, then
   reported in the single outcome after render inspection.
5. A Slack photo keeps its original composition until the exact frame is known.
   Gable then crops and resizes once. A very small upload remains at no more
   than 2x over a blurred, darkened fill made only from that same photo; no image
   model invents property detail.
6. Gable copies the template, fills standalone fields, reads every value back,
   places the hero and headshot, fits only text it changed, renders a thumbnail,
   and asks the configured vision model to compare the supplied property photo
   with the visible result.
7. Only a confident pass is linked as ready. The output is a live Slides file,
   so Carmen can also correct it directly. A rejected draft stays internal and
   its bad link is not offered in Slack.

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
- Keep an agent portrait as one separate shape or image beside the agent name
  and at least one phone, email, or title field. Gable ignores square images
  without that contact-card evidence and refuses multiple portrait candidates.
- Give normal addresses up to 52 average characters, emails up to 42, and agent
  names up to 28 enough room above the 8-point readability limit. These are
  certification targets; each listing is measured again and resized from the
  intended type size only when its actual content needs it.

The first template-folder scan adopts existing files silently. Every later new
file is measured once and gets its own Gable-owned Slack thread. After changing
the source, reply in that thread that it is updated; Gable reloads the same Drive
file and checks it again. Listing-specific text and crop adjustments never need
approval; structural and unreadable problems remain non-overridable.

Two-agent roles can be read from the form notes, but role-specific text and
portrait objects are not certified yet. Those requests pause before copying a
template rather than inferring placement from page order.

## Slack operations

Only the stable user IDs in `GABLE_SLACK_ALLOWED_USER_IDS` can mention, reply
to, upload to, or operate Gable. It names people rather than accounts, and a
person may hold more than one: Carmen posts from a Calvo Consulting guest
account, so both of hers are listed. Anyone absent is dropped in silence at the
first check in each handler, which looks exactly like Gable ignoring them. There are no slash commands and no
operator console in Slack. Mention `@Gable` to start a conversation, then reply
normally inside the Gable-owned thread. Natural requests such as “can you rerun
this project?” reload the current source and continue the same paused listing;
ambiguous instructions produce one clarifying question instead of a guess.

Polling starts with the service when `GABLE_POLL_ENABLED=true` and follows the
configured schedule. A Slack
user cannot pause it, force it, list internal state, or start arbitrary retries.

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
the event or scope set changes. Production startup requires the three Drive
locations, the OpenAI key used by the fail-closed visual gate, and exactly two
allowed Slack user IDs; legacy Slack OAuth client variables are rejected.

## Verification

`make check` is the definition-of-done gate: Ruff formatting and lint, strict
Mypy, and the full Pytest suite.

[`TESTING.md`](TESTING.md) maps each feature to an exact automated or live test,
including the safe one-row Slack-to-Slides workflow in the playground channel.

To exercise the real new-template path without touching the response Sheet or
posting to Slack, copy and inspect one existing source through a temporary
SQLite database:

```bash
.venv/bin/python tools/template_smoke_test.py --source Sold
```

The tool creates one recoverable Drive copy, runs the production triage logic,
prints the result locally, and moves only that temporary copy to Drive trash in
a `finally` block.

`tools/reconcile_image_reservation.py` remains only for auditing and reconciling
historical reservation rows from the retired image-provider experiment. Current
photo fitting creates no such reservation and never calls the tool.

Before enabling polling on an existing database, deploy and restart once with
polling disabled so migrations finish, then run the read-only gate:

```bash
.venv/bin/python -m tools.preview_poll --expect-none
```

If it reports rows that predate activation, review each timestamp, request and
address. `tools.adopt_rows` previews a fail-closed `ROW:CONTENT_HASH` assertion
and writes nothing by default. Re-run the exact same command with `--commit`
only after every row is confirmed historical; it records terminal `skipped`
runs but creates no flyer or Slack message. Run `preview_poll --expect-none`
again, set `GABLE_POLL_ENABLED=true`, restart, and verify the service log. A
nonempty preview is a stop condition.

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
- A hard cumulative $50 ledger guards the connected Firecrawl, conversation,
  and visual-inspection calls. Property-photo fitting has no image-generation
  call. A listing still has a hard ceiling of three fresh run attempts.
