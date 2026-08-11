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
| Sold | 2 | Test_2.jpg 275x183 | NOT CERTIFIED | I rendered it, but agent headshot overlaps and covers the speech-tail shape from the main property photo on the right. |
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
  headshot replacement itself: `find_headshot_frame` chose a frame on this design
  that sits under decorative artwork, so the face covers it. The frame finder
  needs to prefer a candidate that nothing overlaps, or skip when the design's
  own artwork sits on top.
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
