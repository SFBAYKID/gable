# Spike A — can an uploaded file carry an image URL into Bulk Create?

**Status: BLOCKING.** Nothing in Phase 1 gets written until this is answered.
**Who runs it: Chase.** It needs a logged-in Canva session, and I do not enter
credentials (CLAUDE.md §3).
**Time: about 10 minutes.**

---

## The question

`CLAUDE.md` §4.3 item 1. You confirmed by direct observation that Bulk Create's
**manual** data-entry table has "Add text" and "Add image" buttons, and that
adding an image column produces a distinctly image-typed column.

That observation does **not** extend to the **upload** path. Phase 1's entire
deliverable is a file Carmen uploads, so:

> When Bulk Create ingests an uploaded CSV or XLSX, can a column of HTTPS image
> URLs be connected to an **image placeholder** in the design — or can uploaded
> columns only ever fill **text** fields?

If uploaded files carry only text, `canva/bulk_export.py` cannot produce a flyer
with a photo on it, and Phase 1 changes shape. That is a §2.6 "stop and report"
item, not something I redesign around quietly.

---

## Test files

Two files, because the CSV/XLSX difference is itself unverified
(`ARCHITECTURE.md` §2.4 assumes XLSX is the better format — that assumption is
part of what we're testing).

Generate both:

```bash
cd /Users/chasengonzales/real_estate_automator
.venv/bin/python spikes/make_spike_a_files.py
```

That writes `spikes/out/spike_a.csv` and `spikes/out/spike_a.xlsx`, each with
two rows and these columns:

| Column | Row 1 | Row 2 |
|---|---|---|
| `address` | 123 Anywhere St, Any City, ST 12345 | 456 Oak Ave, Any City, ST 12345 |
| `price` | $1,200,000 | $845,000 |
| `agent_name` | Jane Doe | John Smith |
| `photo_url` | *(a public HTTPS .jpg)* | *(a different public HTTPS .jpg)* |

The two photo URLs are **visibly different images** on purpose — a green
mountain landscape and a canal streetscape. If Bulk Create accepts them, row 1
and row 2 must show different pictures. That is how we know it actually fetched
the URLs rather than reusing the template's placeholder image.

Neither default photo is a house. That is deliberate and it does not weaken the
test: the question is whether Canva pulls a URL into an image frame at all, not
what the photo depicts. It will look silly. Ignore that.

The script **verifies both URLs before writing anything** and refuses to produce
files if either is unreachable — see the last section for why that guard exists.

---

## Steps

1. Open Canva. Open the flyer template you'd actually use — one that has a real
   **image placeholder** (a photo frame), not just text boxes. The scratch design
   "White Beige Modern House for Sale Flyer" from your testing works if it has a
   photo frame.
2. **Apps → Bulk create → Upload data (CSV/XLSX)**. Upload `spike_a.xlsx`.
3. Look at how Canva presents the four columns. **Screenshot this screen.** The
   specific thing to look at: is `photo_url` shown with the same `T` text glyph
   as the other three, or with the image glyph you saw on the manual table?
4. Now try to connect the data to the design. Right-click / click the **image
   placeholder** in the flyer and look for "Connect data".
   - **Does `photo_url` appear in the list of fields offered for the image?**
   - Or does the image placeholder offer nothing, while only text boxes can be
     connected?
5. If you can connect it: generate both pages.
6. **Screenshot both generated pages.**
7. Repeat steps 2–6 with `spike_a.csv`. It may behave differently from the xlsx;
   that difference is worth knowing.

---

## What to report back

Answer these five. Short answers are fine — but please say what you *saw*, not
what you concluded.

1. **Did the image placeholder offer `photo_url` as a connectable field?**
   Yes / No / It offered something else — describe it.
2. **Did the generated pages show the two different photos from the URLs?**
   Yes / No / It showed the template's placeholder image on both.
3. **Did the xlsx and the CSV behave the same way?**
4. **Did Canva show any error, warning, or upgrade prompt** at any point?
   Verbatim text if so.
5. **Screenshots**: the column-mapping screen, and the generated pages.

While you're in there, two free answers that cost nothing extra and knock out
two more §4.3 unknowns:

6. **Any stated row limit?** Bulk Create sometimes names a maximum on the upload
   screen or after generating (§4.3 item 2).
7. **Did the layout hold?** Row 1's address is long. Did the text overflow its
   box, shrink, or clip (§4.3 item 3)?

---

## What each outcome means

**PASS — image placeholder connects to `photo_url`, both photos render.**
Phase 1 proceeds as designed in `ARCHITECTURE.md`. `bulk_export.py` writes an
xlsx (or CSV, if only that worked) with a `photo_url` column pointing at
DigitalOcean Spaces. I start Phase 1 immediately.

**PARTIAL — CSV works but XLSX doesn't, or vice versa.**
Phase 1 proceeds; I emit whichever format worked, and `ARCHITECTURE.md` §2.4's
reasoning gets a correction row in the decision log.

**FAIL — uploaded columns can only fill text fields.**
I stop and report; I do not design around it. The options at that point, none of
which I pick unilaterally:

- Carmen uploads the photos to Canva by hand and Bulk Create fills only the
  text. Saves most of the typing, none of the photo hunting — which is where her
  twenty minutes actually goes, so this is a real loss, not a small one.
- Move Phase 2 (the data-connector app) forward, since the manual/connector path
  *is* confirmed to support image cells. That is gated on §4.3 item 4 —
  whether a private app ships to a Teams team without marketplace review — and
  it is a TypeScript project, which the current goal explicitly excludes.
- Revisit Canva Enterprise for the Connect API. Real money; your call alone.

This is exactly the branch README.md warned about: *"The fix is probably moving
Phase 2 forward, not working around it."*

---

## The false negative this spike is built to avoid

The test needs two **publicly reachable HTTPS image URLs**, and they have to be
reachable *by Canva's servers*, not just by your browser.

My first choice was Wikimedia Commons. I tested it and it does not work:
`upload.wikimedia.org` returns **HTTP 403** to non-browser User-Agents and
**HTTP 429** to browser ones. Canva's image fetcher sends its own User-Agent,
so a Wikimedia URL could fail for reasons that have nothing to do with Bulk
Create — and we would read that as "uploaded files can't carry images," stop
Phase 1, and be wrong. That is a day lost to a wrong answer, which is worse than
no answer.

The defaults are Lorem Picsum instead (`picsum.photos`), which does no
User-Agent gating. Verified 2026-08-10: both URLs return HTTP 200 with
`Content-Type: image/jpeg`.

**If you already have the Spaces bucket up, use it instead** — those are the
URLs production will actually emit, so the spike then tests the real thing:

```bash
.venv/bin/python spikes/make_spike_a_files.py \
    --photo-url https://gable-photos.nyc3.digitaloceanspaces.com/a.jpg \
    --photo-url https://gable-photos.nyc3.digitaloceanspaces.com/b.jpg
```

Either way, if the spike fails, the first question is "did Canva reject the
*column*, or just fail to fetch the *URL*?" Those look identical from the
outside and mean opposite things. A second run against a different host settles
it — please do that before we conclude anything.
