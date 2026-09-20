# Gable — the photo boundary

Moved out of `ARCHITECTURE.md` §4.5 and §4.5b on 2026-09-20, when that file
reached the 800-line ceiling for the third time — the decision log was the
first and the conversation design the second. This is how a supplied photograph
becomes something Slides will fetch, and how it is fitted to a measured frame.
Nothing below is edited in the move except the section numbering it keeps.

`ARCHITECTURE.md` §4.4 is where the photographs are asked for, and
`pipeline/property_photos.py` is what places a batch of them.

### 4.5 Store photo (`photos/store.py`)

Slides needs a publicly fetchable image URL. Verified against Google's API
reference on 2026-08-10: max 2 kB of URL, 50 MB, 25 megapixels, and PNG, JPEG or
GIF. Fixed provider limits stay in the image boundary instead of operator
settings.

Two consequences worth stating plainly, because both have bitten this design:

- **A Drive link will not work — and not for the reason we assumed.** The old
  text here said Drive fails because it requires auth. That is only half true,
  and the real answer was established by experiment on 2026-08-10:

  | URL form | Anonymous `GET` | Historical Slides replacement experiment |
  |---|---|---|
  | `picsum.photos/….jpg` (control) | 200, valid JPEG | **accepted**, `occurrencesChanged: 1` |
  | `drive.google.com/uc?export=view&id=` | **200, `image/png`, valid bytes** | rejected — *"problem retrieving the image"* |
  | `drive.google.com/uc?export=download&id=` | **200, `image/png`, valid bytes** | rejected — same |
  | `drive.google.com/thumbnail?id=…&sz=w1600` | 404 | rejected — *"image was not found"* |

  The service account **can** publish a Drive file (`role: reader, type: anyone`)
  and the result **is** genuinely fetchable by an anonymous client. Slides still
  refuses it. So this is not a permissions problem that more sharing would fix —
  Slides declines to fetch from Drive, full stop. A separate public host is
  mandatory, not merely tidier. The control in the same batch rules out a broken
  test harness.

- **The URL only has to survive one moment.** Slides fetches the image once at
  insertion and stores a copy inside the presentation, so a post does not break
  later when the source URL expires. That makes short-lived hosting fine, and it
  means the host needs no durability guarantees at all.

Options, in preference order:

1. **The droplet, over plain `http://`** — in use, and the reason there is no
   critical path here any more. Slides was assumed to require https; it does
   not, verified live. nginx serves `/var/www/gable-photos`. Production writes
   there locally under the systemd unit's narrow `ReadWritePaths`; development
   may still use the SSH publisher. It costs nothing beyond a droplet already
   paid for. A photo only has to survive one fetch, so the host needs no
   durability.
2. An object store if photo hosting ever outgrows one box; none is connected.
3. Google Drive public links — **do not, and now we know why.** See the table.

Normalise before upload: convert to JPEG, apply EXIF orientation, strip metadata,
and reduce only an unnecessarily large edge. Do not crop to the slide canvas;
the exact hero frame is not known yet. Slack download size is hard-capped at 25
MB before Pillow opens it. The derivative is content-addressed and atomically
published.

### 4.5b Fit the photo to the frame (`photos/fit.py`)

**This is the hardest problem in the product.** Everything else is plumbing;
this is the part that decides whether the output looks professional or obviously
machine-made.

A photo an agent shot on their phone is the wrong aspect ratio, often the wrong
exposure, and never composed for a 1080 × 1350 frame with a text panel across the
bottom third. Scaling it naively produces a stretched house, or a roofline
guillotined at the top — errors that are glaring to a client and invisible to a
script checking that the file is a valid JPEG.

The common path is deterministic. Pillow center-crops once to the **measured
hero frame** and resamples to that frame's pixel dimensions. Up to a 2x
enlargement stays local. Crop loss above 30 percent becomes a note in the one
post-build outcome; it never creates an approval question. The rendered vision
gate still blocks delivery if the automatic crop removes important content.

When full-frame cover would exceed 2x, Pillow makes a blurred, darkened cover
from the source and centers a complete foreground copy at no more than 2x. The
result is the exact frame size, preserves every source edge, invents no property
detail, makes no provider call, and records `ai_enhanced=0`. The original Slack
upload is never overwritten; rendered vision remains the delivery gate.

Synthetic property-photo generation is not connected. The database retains the
disclosure flag required by the runtime contract, but the running system has no
generator or approval flow and never claims otherwise.
