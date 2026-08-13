# Testing Gable

This is the repeatable verification guide for the product as it exists now.
Slack is natural-language only; none of these checks uses a slash command.

## Safety boundary

All live Slack testing goes to `C0B02721MNK`, monarch-bot-playground. Never run
a test against `C0BP597644B`, the production channel. The form-response tab is
read only. A live flyer test creates a Slides output in Gable's shared drive but
does not publish, export, or share it outside Gable.

Before a live Slack test, confirm the droplet is configured for the playground:

```bash
ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
  "sed -n 's/^GABLE_SLACK_CHANNEL_ID=//p' /opt/gable/.env"
```

The only acceptable output for a test is `C0B02721MNK`. Stop if it is anything
else. This command reads only the channel setting; it does not print tokens.

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
  "Form Responses 1" 47 --dry-run'
```

This proves header discovery and form parsing without opening a run or posting
to Slack. As verified on 2026-08-12, line 47 currently reads:

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

## 6. Full form-to-Slides test

This is the release gate. Leave the Gable service running so Socket Mode can
receive the photo reply. Start the chosen row on the droplet, where the command
shares the same database as the listener:

```bash
ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
  'cd /opt/gable && sudo -u gable .venv/bin/python -m tools.run_row \
  "Form Responses 1" 47'
```

Expected first half:

1. Gable posts one channel announcement naming `Sold`, Mike Kulnich, and
   703 Perception Way, Aberdeen, MD 21001.
2. Gable replies to that announcement with “Can you send me the image?”
3. The database run is `needs_photo`, and the Slack root timestamp belongs to
   that run.

Upload exactly one property image as a reply to Gable's question. Do not start
a new channel message. The owned-thread upload should automatically:

1. download the Slack file without altering the original;
2. retain its composition until the `Sold` hero frame has been measured;
3. crop and resize once to that frame, using at most one guarded enlargement;
4. reload the exact `Sold` source from Generic Templates;
5. fill the address and Mike's roster-backed contact and headshot fields;
6. preserve the template's original layering around the replacement images;
7. read the inserted values back from Slides;
8. render Google's result and compare it with the supplied property image;
9. post one final outcome in the same thread.

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
Read the precise Slack explanation, fix the code or source responsible, and run
a fresh controlled row only after the cause is understood.

## 7. Repeating live tests

Do not delete run rows or edit the response tab to make a test repeatable. A
submission has a hard three-attempt ceiling, including its adopted historical
attempt. Use a fresh dedicated test response once a row reaches the limit. This
is intentional protection against duplicate flyers and paid retry loops.
