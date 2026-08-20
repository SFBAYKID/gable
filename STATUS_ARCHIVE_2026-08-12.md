# Gable status archive — 2026-08-12 and earlier

Moved out of `STATUS.md` on 2026-08-20 to keep it under the 800-line ceiling.
Nothing here was edited; it is the record as it was written.

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

## Superseded: the 2026-08-11 progress snapshot

Moved to `STATUS_ARCHIVE_2026-08-11.md` on 2026-08-19 when this file reached
the 800-line ceiling. Nothing in it is current.
