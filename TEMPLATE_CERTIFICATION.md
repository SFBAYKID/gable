# Template certification ledger

What has actually been proved, per request type. Updated as runs complete, not
batched at the end, so a session that dies mid-way loses nothing.

**A template is certified only when a rendered flyer has been looked at and
would be handed to a client.** "It rendered without an exception" is not
certification. Nothing below is certified yet.

## Run 2026-08-11 — one real submission per request type

Every row replayed from the live sheet through the whole pipeline: research,
template selection, fill, photo fit and publish, hero placement, headshot
replacement, text fitting, and the vision pass. A different image each time.
Nothing was written to the Sheet.

| Request type | Sheet row | Image | Verdict | What happened |
|---|---|---|---|---|
| Sold (looked at) | 2 | Test_2.jpg 275x183 | NOT CERTIFIED | I rendered it, but agent headshot overlaps and covers the speech-tail shape from the main property photo on the right. |
| Under Contract | 6 | images copy.jpg 678x452 | REFUSED (correct) | This one is marked under contract but there is no closing price on it. Do you have that? |
| Open House | 7 | images-1 copy.jpg 617x324 | NOT CERTIFIED | I rendered it, but top logo is cut off at the top edge. |
| New Listing | 12 | images-1.jpg 678x452 | NOT CERTIFIED | The website is 29 characters and this design fits about 24. Shall I shorten it, or use a different design? |
| New Listing with Open House | 9 | images-2.jpg 738x414 | NOT CERTIFIED | I copied the design, but one of its fields did not match exactly once. I stopped before changing any text. |
| Price Reduction | 4 | images-3.jpg 566x353 | REFUSED (correct) | This is a price reduction, but no new price came through. What is it now? |

**Every request type routed to a design and rendered. Nothing crashed. Nothing
was delivered.**

### The two refusals are correct and should not be "fixed"

`Under Contract` and `Price Reduction` both stopped and asked. A sold listing
with no closing price and a price reduction with no new price are contradictions,
and Chase's rule is to ask rather than render a blank. **These are passes.** If a
future change makes them deliver, that is a regression.

### What the four reviews found, and what each means

* **Sold — the headshot overlaps a speech-tail shape.** A defect introduced by
  headshot replacement itself: `find_headshot_frame` chose a frame that sits
  under decorative artwork, so the face covers it.

  `_is_overlaid` was added to reject such frames and **it does not fix this
  case.** Three attempts, and the reason is now understood: the speech-tail is
  inside an `elementGroup`. Group children do not appear in `pageElements`, and
  the API reports the group's own bounds as zero, so the overlap check cannot
  see the artwork at all. Fixing it means composing group-relative transforms to
  get absolute child bounds — real work, not another threshold, and the same
  blind spot that hides text inside groups from field resolution.

  `absolute_boxes` now composes group-relative transforms so grouped artwork is
  visible to the overlap check. It still does not change this outcome, and
  **looking at the rendered flyer explains why the text description was
  misleading**: the speech-tail is a small olive triangle at the photo's lower
  left, and the portrait's corner clips its tip. It is a minor cosmetic overlap,
  not a broken flyer.

  That is worth recording carefully. The vision pass is deliberately
  conservative, so `needs_review` spans everything from "a field is missing" to
  "one decorative point is clipped". **Reading its verdicts without looking at
  the flyer overstates how bad the output is.** Whether this particular overlap
  matters is Carmen's call, not the pipeline's.
* **Open House — the top logo is cut off at the top edge.** Not yet diagnosed.
  Either the hero frame extends above the slide and the logo is being covered, or
  the template itself crops.
* **New Listing — the website is 29 characters and the design fits about 24.**
  The per-template character budget working exactly as intended: it measured the
  overflow and asked rather than shipping clipped text.
* **New Listing with Open House — a field did not match exactly once.** The
  substring guard working as intended. `replaceAllText` matches substrings, so a
  literal appearing twice would corrupt both; the run stopped instead.

### Coverage still missing

* Only 6 of 45 templates have been exercised, and **0 certified**.
* `Client Review Post`, `End of Year Brag Post` and the lowercase `just listed`
  row are untested. The first has no property at all and must not trigger a
  photo request; the last will fail if routing is case-sensitive.
* Image variety is inadequate: every file in the test folder is small landscape
  jpg. No portrait, square, panorama, PNG, EXIF-rotated, or multi-megabyte
  source has gone through the real path. See `GABLE_TEST_PROMPT.md`.
* No conversational edit has been applied to any of these flyers.

## Photo shapes — proved 2026-08-11

Every reasonable source shape and size through the real `fit_locally` path. All
land at exactly 1080x1350, filling the frame, none seamed.

| Source | Result |
|---|---|
| Landscape phone 4032x3024 | fits |
| Landscape web 1920x1080 | fits |
| Portrait 4:5 1080x1350 | fits |
| Portrait tall 3024x4032 | fits |
| Square 1080x1080 | fits |
| Panorama 4000x1200 | fits |
| Tiny 275x183 | fits, and is enlarged by the model first |
| PNG 1600x1200 | fits |
| Huge 6000x4000 | fits |

## The Slack leg — proved 2026-08-11

The gap named above is now closed. A real photo went the whole way:

| Step | Result |
|---|---|
| Source | 3024x4032 portrait, **EXIF orientation 6**, 1.0 MB — a phone held sideways |
| Uploaded to Slack | Monarch Bot Playground (`C0BP597644B`), not the production channel |
| `files_info` | returned `url_private_download` |
| `download_private_image` | 1.0 MB, **byte-identical** to what was uploaded |
| EXIF handling | applied — the stored 3024x4032 reads back as 4032x3024 upright |
| `fit_locally` | exactly 1080x1350 |
| `publish` to the droplet | succeeded |
| `verify_public` | 200, `image/jpeg` |

So the complete photo path — a person dropping a file in Slack through to a URL
Slides can fetch — is proved, on a multi-megabyte EXIF-rotated portrait, which
is the case most likely to break.

**This posted one message and one file into the playground channel.** That is
the channel designated for testing; nothing was sent to production.

## The Sold flyer, assessed by looking at it

Real listing photo fitted correctly from a landscape source, the agent's real
headshot, name on one line above its title, the office number supplied by the
fallback rule, complete address, complete price, every field populated and no
placeholder surviving.

**The only defect is the clipped decorative point described above.** This is the
closest the pipeline has come to a deliverable flyer, and the remaining question
is a judgement about a design flourish rather than a pipeline failure.

Still not certified, because certification means Carmen looked at it. But the
gap between this and deliverable is now one cosmetic overlap, not a list.

## All 45 templates walked — 2026-08-11

Every template forced through the pipeline with a valid Sold submission and a
real photo. Not a sampling: all 45.

| Outcome | Count |
|---|---|
| delivered | 2 |
| needs_review | 41 |
| failed | 2 |

### "Delivered" does not mean correct, and this is the finding that matters

`Open House — Two Agents, DM Me` was **delivered** — it passed every check
including the vision pass, and Gable said "Your flyer is ready."

Looking at it:

* The price reads **`$460,0000`** — four zeros. The submission supplied
  `$685,000`. The flyer carries a **malformed, wrong price on a real address**,
  which is the single worst failure this system can produce.
* The hero photo is **the template's own sample house**, not the supplied
  photo. It was never replaced, and it sits letterboxed with white margins.

Everything else about it is genuinely good — both agents' real headshots, real
names and numbers, correct address, clean type. That is exactly what makes it
dangerous: it looks finished.

**So the delivered count is not a measure of success.** Two flyers passed the
gate and at least one of them is wrong. The vision pass reads layout, and a
plausible-looking wrong number is not a layout problem. Nothing currently checks
that a value on the flyer equals the value that went in.

**The fix is a readback check**, not more vision prompting: after filling, read
the slide text and assert every value the run supplied appears exactly as
supplied. That would have caught this deterministically, and it is the same
class of guard as the substring check that already refuses a literal matching
more than once.

### The 41 reviews cluster into five causes

| Count | Cause |
|---|---|
| 13 | a literal did not match exactly once — the substring guard refusing |
| 9 | placeholder text survived (TIME, CITY STATE, NEIGHBORHOOD NAME) |
| 8 | element overlap |
| 6 | no photo frame found — the 12 templates hero measurement cannot resolve |
| 3 | other |
| 2 | clipped at an edge |

Fixing the first two causes would move roughly half the deck. Neither is a
per-template job.

### 2 failed outright

`Coming Soon — Something Great Is On the Way` and
`Open House — Two Agents, Two Dates` raised rather than returning a status. Not
yet diagnosed.

## Re-walk with the readback guard — 2026-08-11

| Outcome | Before | After |
|---|---|---|
| delivered | 2 | 1 |
| needs_review | 41 | 44 |
| failed | 2 | 0 |

The two failures are gone and the delivery count dropped, which is the guard
working: fewer flyers pass, and the one that does was checked.

`Open House — Two Agents, Two Dates` delivered with the **correct** price
(`$685,000`) and address, the supplied photo placed, and the agent's real
headshot. Verified by reading the slide text and by looking at it.

### The guard's blind spot, found by looking at the flyer it passed

That same delivered flyer carries **`Stacey Abbott`, `410.952.6193`,
`sabbotthomes@gmail.com`** — sample data from the template's second agent slot.
The open-house dates are the template's too (August 8 and 9), not the submitted
time.

The readback guard cannot catch this. It verifies that **supplied values
appear**; it has nothing to compare against for a value that was never supplied.
On a two-agent design where only one agent is known, the other agent's sample
identity survives intact and looks entirely real.

**This is the same severity as the wrong price.** A flyer going out with a
stranger's phone number and personal email on it is worse than a layout defect,
and it passed every check.

**What would catch it:** the same list that already powers
`fields.SAMPLE_AGENT_NAMES`, extended to sample phone numbers, emails and dates,
asserted as an absence check after filling — no known sample value may remain on
a delivered flyer. Two-agent designs additionally need either a second agent
from the submission or a refusal, because a design with a slot Gable cannot fill
is not a design Gable can finish.

### Where certification actually stands

**0 of 45 certified.** One template delivers with correct supplied values and
still carries another person's contact details. Certification remains Carmen's
judgement and she has not seen any of these.

## After the resolver fix — 2026-08-11, final walk of the session

| Outcome | First walk | Now |
|---|---|---|
| delivered | 2 (one carrying a wrong price) | 1, verified correct |
| needs_review | 41 | 39 |
| failed | 2 | 0 |

Remaining review causes: 8 placeholder survived, 8 no photo frame, 6 overlap,
6 substring guard (down from 13), 5 clipped, 3 foreign contact, 3 other.

### `Just Listed — Book a Tour, DM for Details` — the first flyer worth posting

Read back and looked at. Everything on it is right:

* address `13838 Dayton Meadows, Dayton, MD 21036`, complete
* `Offers from $685,000`, the supplied price, correctly placed
* 5 beds / 2.5 baths / 3,070 sq ft — **researched from the web**, nobody typed them
* the agent's real name, headshot, phone and email
* the supplied hero photo filling its frame
* clean layout: nothing clipped, overlapping, or shrunk

The only blemishes on it are the two **template** defects already logged for
Carmen in `TEMPLATE_ISSUES.md`: the "approch" typo in the footer and the
low-contrast logo sitting over the photo. Neither is Gable's to fix, and both
are in the design.

**This is the first flyer in the project that a person could post.** It is still
not *certified* — certification means Carmen looked at it — but the gap is now
her judgement rather than a list of defects.

### What the guards are worth

Three of them fired on real problems during this walk that would otherwise have
shipped: the readback caught a value that did not survive filling, the absence
check caught three flyers carrying a phone number belonging to someone else, and
the substring guard caught six literals embedded in longer text. None of those
are visible to a person skimming a thumbnail.

## Frame coverage after measuring the deck properly — 2026-08-11

| | Was | Now |
|---|---|---|
| hero frame found | 33/45 | **39/45** |
| headshot frame found | — | **37/45** |

The hero rule required a photo well to span 60% of the slide width, on the
belief that the hero is always a full-bleed top band. Twelve designs put it in a
partial-width block instead — 45%, 51%, 54%, 57% wide, all anchored at the top.
Those are photos. The headshot frames on the same designs measure 21% to 34% and
sit two thirds of the way down, so the two groups are well separated and the
threshold now sits between them at 40%.

Headshot detection was 9/45 and the cause was measured rather than guessed:
**29 of the 45 were rejected by the overlap check**, which is what the guess
would have been, but measuring turned it into a specific fix rather than a
threshold nudge.

The check rejected a frame if *any* element intersected it. Only elements drawn
**on top** can be covered by a photo placed into the frame, and `pageElements`
is in z-order — so everything before the frame sits behind it and is irrelevant.
A headshot well naturally sits over a background panel it is in no danger of
hiding. Considering only what is above took it to **37/45**.
