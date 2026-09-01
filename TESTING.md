# Testing Gable

This is the repeatable verification guide for the product as it exists now.
Slack is natural-language only; none of these checks uses a slash command.

## Safety boundary

All live Slack testing goes to `C0B02721MNK`, monarch-bot-playground. Never run
a test against `C0BP597644B`, the production channel. The form-response tab is
read only. A live flyer test creates a Slides output in Gable's shared drive but
does not publish, export, or share it outside Gable.

Gable is now live in `C0BP597644B` for Carmen, so **do not edit
`GABLE_SLACK_CHANNEL_ID` in `/opt/gable/.env` to run a test.** That is what this
section used to say, and it is no longer safe: it takes Gable off the production
channel for the length of the test, and a real submission arriving in that
window would be announced to the playground instead of to Carmen.

Override the channel for the one process instead. `Settings` is read from the
environment, so a variable set on the command line wins over the unit's
`EnvironmentFile` without touching the file or restarting the service:

```bash
ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
  'cd /opt/gable && sudo -u gable env GABLE_SLACK_CHANNEL_ID=C0B02721MNK \
     /opt/gable/.venv/bin/python -m tools.run_row "Testing_1" <row>'
```

Everything that run posts goes to the playground; the listener keeps serving
production. Confirm the file was not changed afterwards:

```bash
ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
  "sed -n 's/^GABLE_SLACK_CHANNEL_ID=//p' /opt/gable/.env"
```

That must still print `C0BP597644B`. It reads only the channel setting and
prints no tokens.

**A conversational test cannot be driven from Slack.** `routing.py` drops every
event carrying a `bot_id`, and `GABLE_SLACK_ALLOWED_USER_IDS` lists three human
ids, so a scripted post is ignored no matter which token sends it. Either have a
listed human type the replies, or drive the two calls the listener makes —
`brain.think`, then the handler its decision names — which exercises the same
conversation without Slack's event plumbing.

## 0. The real-address corpus

`tests/fixtures/address_corpus.tsv` holds every address the form has ever
received, with what Gable makes of each one. `tests/test_address_corpus.py`
holds the code to it. After any change to `listings/address.py` or
`slides/manifest.py`, and every week or so regardless, refresh it and read the
diff before committing:

```bash
PYTHONPATH=src .venv/bin/python tools/refresh_address_corpus.py
```

It reads the response tab through the service account and never writes to it.
A verdict that changed is a fix or a regression, and the diff is where you
decide which. A new row is a new real input the suite now knows about.

## 1. Complete local gate

From the repository root:

```bash
make check
```

This must finish with all three gates green:

- Ruff formatting and lint;
- strict Mypy over `src`, `tests`, and `tools`;
- the full hermetic Pytest suite.

The suite covers intake by header name, address normalization, run-state and
retry ceilings, template selection and preflight, exact Slides requests, hero
and headshot placement, text fitting, visual-review refusal paths, Slack thread
ownership, natural-language intent, native waiting status, photo handoff,
style enforcement, polling, spending limits, and deployment configuration.

Useful focused commands while diagnosing one area are:

```bash
.venv/bin/pytest tests/test_slack_brain.py tests/test_slack_routing.py
.venv/bin/pytest tests/test_slack_status.py tests/test_slack_photos.py
.venv/bin/pytest tests/test_runner.py tests/test_pipeline_live.py
.venv/bin/pytest tests/test_slides_preflight.py tests/test_pipeline_vision.py
.venv/bin/pytest tests/test_template_triage.py tests/test_template_vision.py
```

These are subsets, not substitutes for `make check`.

## 2. Live connection check

After any credential, scope, folder, or deployment change, run this on the
machine whose `.env` is being tested:

```bash
.venv/bin/python tools/check_connections.py
```

It makes the cheapest real request to Slack, Socket Mode, Google, Firecrawl,
and OpenAI. It prints identities and pass/fail results, never credential values.
It does not post to Slack or modify the response Sheet.

## 3. Read one real form response without acting

Run this on the droplet so it uses the production Google configuration:

```bash
ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
  'cd /opt/gable && sudo -u gable .venv/bin/python -m tools.run_row \
  "Testing_1" 48 --dry-run'
```

This proves header discovery — including Testing_1's blank first row and split
first/second agent name — and form parsing without opening a run or posting to
Slack. As verified on 2026-08-13, line 48 currently reads:

- Mike Kulnich;
- Sold;
- 703 Perception Way, Aberdeen, MD 21001;
- closing price 615000;
- seller side.

The live row says Mike Kulnich, not Mike Clunch. A correct test preserves what
the form actually says rather than silently correcting the name.

## 4. Test a source template without Slack

With the configured Google and OpenAI credentials:

```bash
.venv/bin/python tools/template_smoke_test.py --source Sold
```

This copies `Sold`, runs the production deterministic and visual triage path,
prints the verdict locally, and moves only the temporary copy to Drive trash in
a `finally` block. It never reads or writes the response Sheet and never posts
to Slack. It may make one spend-gated visual-inspection call.

Pass criteria:

- exactly one source named `Sold` is found;
- structural measurement completes;
- any warning names a real measured limitation;
- the temporary file is moved to trash even when inspection fails.

## 5. Natural-language Slack behavior

Run these only in monarch-bot-playground.

1. Mention `@Gable` and say “hello.” Gable should open a thread, show the native
   purple waiting state immediately, answer briefly, and clear the state.
2. Reply normally in that Gable-owned thread. No repeated mention should be
   necessary.
3. Say “update the image.” Gable should ask whether you mean the hero image or
   headshot and change nothing.
4. In a paused listing thread, say “Hey, can you rerun this project?” Gable
   should reload the current source and continue the same run. It must not open
   a duplicate attempt.
5. Reply without mentioning Gable inside a thread owned by another app. Gable
   must remain silent. An explicit mention may receive one answer, but it does
   not transfer ownership of that thread.
6. Upload a top-level image with no Gable-owned thread. Gable must not guess
   which listing it belongs to.

Every visible reply must contain no emoji, raw errors, pasted URL, placeholder,
or machine-facing field name.

Polling was enabled on 2026-08-14 and the droplet now runs with
`GABLE_POLL_ENABLED=true`. The gate that let it be switched on is the one to
repeat after any identity change or database restore: run
`.venv/bin/python -m tools.preview_poll --expect-none` on the droplet, which is
read-only and must report zero unhandled rows. If it does not, review and
explicitly adopt only confirmed historical `ROW:CONTENT_HASH` pairs with
`tools.adopt_rows`; preview first, commit the same assertions, and repeat the
zero-candidate gate. Never leave polling on over a nonempty preview — twelve
historical rows would have become twelve flyers.

## 6. Full form-to-Slides test

This is the release gate. Leave the Gable service running so Socket Mode can
receive the photo reply. Start the chosen row on the droplet, where the command
shares the same database as the listener:

```bash
ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
  'cd /opt/gable && sudo -u gable .venv/bin/python -m tools.run_row \
  "Testing_1" 48'
```

Expected first half for a source with no current structural blocker:

1. Before posting, Gable checks Mike's submitted name and email against Agents
   Contact Information and confirms that a direct phone is present. Only a
   missing workbook value may be resolved from Mike's one exact profile on the
   official Corner House Realty domain; any source discrepancy stops as
   `needs_info` without changing either source. Because Sold carries a REALTOR
   credential field that the workbook does not collect, the same exact profile
   must also prove that title before Gable asks for the image.
2. Gable posts one channel announcement naming `Sold`, Mike Kulnich, and
   703 Perception Way, Aberdeen, MD 21001.
3. Gable replies to that announcement with “Can you send me the image?”
4. The database run is `needs_photo`, and the Slack root timestamp belongs to
   that run.

If the current Sold source makes the address, agent name, title, phone, or email
tight, Gable computes and applies the largest readable fitted size itself. It
does not ask Chase to resize the template or approve a harmless text fit. It
pauses only if the result would reach the readability floor or the source
geometry cannot be measured safely. The same run then asks for the image and
resumes in place.

Upload exactly one property image as a reply to Gable's question. Do not start
a new channel message. The owned-thread upload should automatically:

1. download the Slack file without altering the original;
2. retain its composition until the `Sold` hero frame has been measured;
3. fit once to that frame; beyond 2x, keep the complete foreground at no more
   than 2x over a blurred, darkened fill made from the same upload;
4. reload the exact `Sold` source from Generic Templates;
5. fill the address and Mike's roster-backed contact and headshot fields;
6. preserve the template's original layering around the replacement images;
7. read the inserted values back from Slides;
8. render Google's result and compare it with the supplied property image;
9. post one final outcome in the same thread.

A crop loss above 30 percent follows the same rule as readable text: Gable
center-crops and builds without asking for approval, reports the adjustment in
that one outcome, and lets the rendered vision inspection decide whether the
result is safe to deliver.

The current `Sold` source has no price field, so the submitted closing price is
correctly absent rather than forced into an unrelated object.

The test passes only when the final run status is `delivered`, the closing
message contains a descriptive Slides link, and a human opening that link
confirms all of the following:

- the property photo is the supplied property and is neither stretched nor
  visibly pillarboxed;
- the important part of the property is not cropped away;
- the address is correct and fully visible;
- Mike's name, email, phone, and headshot are correct wherever the source calls
  for them;
- no sample value, unresolved label, overlap, clipping, or off-canvas object is
  visible;
- the file remains an editable Google Slides presentation.

`needs_review` is a correct safety stop, but it is not a passing release result.
Read the precise Slack explanation and fix the responsible code or source. A
rejected draft remains recorded in Drive but its link must not appear in Slack.

If inspection confidently proves a contradiction independently visible in the
original supplied image, expect one message asking for the correct property
image and status `needs_photo`. A number not legible in the original cannot be
used as source evidence. Upload one replacement in the owned `needs_photo` thread;
it must resume the same run and attempt count, then pass before delivery.

## 7. Repeating live tests

Do not delete run rows or edit the response tab to make a test repeatable. A
submission has a hard three-attempt ceiling, including its adopted historical
attempt. Use a fresh dedicated test response once a row reaches the limit. This
is intentional protection against duplicate flyers and paid retry loops.

Property-photo fitting never calls an image provider and does not create an
image-operation reservation. Historical reservation rows from the retired
provider experiment remain append-only evidence; they are not part of repeating
a current test. Resume a paused source, information, or review run through its
owned Slack thread. `tools.run_row --resume` refuses `needs_photo`: upload the
new property image in that owned thread so the rejected audit image cannot be
silently reused.

---

## 9. Driving a campaign cell by hand (2026-08-14)

One cell is one salesperson against one design. The house number identifies the
cell so feedback can name it — "the 104 one" is Andy Jang's Open House.

1. **Seed and start.** `tools/seed_test_rows.py` appends the row; it refuses any
   tab not named `Testing*`. Then `python -m tools.run_row Testing_1 <row>` **on
   the droplet**, so the run shares the listener's database.
2. **Answer in the thread.** Text can go through any Slack client as Carmen or
   Chase. The photograph has to be a real upload, because a bot's file share is
   ignored by design.
3. **Compare the render against its source design before judging it.** Chase's
   standing instruction. A defect present in the source is Carmen's, not
   Gable's, and the difference decides whether it is a bug at all. Render both
   with `pages.getThumbnail` and look at them side by side.
4. **When a design's photo lands wrong, do not reason about the geometry.**
   Copy the design, delete one candidate shape, render, look. Three of the six
   designs turned out to be replacing the wrong shape, and each was settled this
   way in a minute. The API cannot tell you which shape carries the picture:
   every one of them reports an empty fill.
5. A cell passes only on a clean run with no intervention. A run that needed a
   patch mid-flight is a failed run; the rerun after the fix is what counts.

### Rerunning

A submission allows three attempts, ever. Seed a fresh row rather than trying to
reuse a spent one — byte-identical rows get distinct identities by design.
