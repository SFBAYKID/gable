# Template issues — for Carmen

Defects that live in the **Google Slides / PPTX templates themselves**, not in
Gable's code. Chase reviewed two rendered flyers on 2026-08-10 and separated
these out explicitly:

> "Not in scope for you: the 'approch' typo, the mixed typefaces, the
> low-contrast logo, and the panel misalignments. Those are template defects.
> Log them in a TEMPLATE_ISSUES.md for Carmen; do not attempt to work around
> them in code."

**That instruction is binding on every future agent.** Gable must not
special-case, patch, or paper over anything on this list. A code workaround for
a design defect hides it from the person who can actually fix it, and it breaks
the moment the template is re-exported. Fix the design in Canva, re-export, and
re-upload to Drive.

## Status

Each item is: **Open** (seen, not fixed), **Fixed** (corrected in the source
design and re-uploaded), or **Won't fix** (deliberate).

---

## 1. Spelling: "approch" — Open

The word **approch** appears in template body copy. It should be **approach**.

Where seen: rendered flyer reviewed 2026-08-10 (`bad 1.png`).
Impact: a misspelling reaches a client-facing flyer. Highest-priority item here.

## 2. Mixed typefaces within one design — Open

A single flyer uses more than one type family where it reads as accidental
rather than as a deliberate pairing. The stats row and the body copy do not
match the headline family.

Where seen: rendered flyers reviewed 2026-08-10 (`bad 1.png`, `Bad2.png`).
Impact: looks unfinished. A designer notices immediately.

Note for whoever fixes it: Gable's text-fitting pass shrinks type to stop
overflow, and its width estimates are calibrated per character class, not per
font. A design using an unexpectedly wide display face may be shrunk a point or
two more than intended. Consistent type here helps the automatic fitting too.

## 3. Low-contrast logo — Open

The Corner House logo sits on a background close enough in value that it nearly
disappears. Fails ordinary legibility at flyer size, and worse in a Slack
thumbnail preview.

Where seen: rendered flyers reviewed 2026-08-10.
Fix direction: use the reversed (white) logo lockup over the dark panel, or move
the logo onto a lighter area of the design.

## 4. Panel misalignment — Open

Rectangular panels behind the contact block and the stats row do not align to a
shared grid. Edges are off by a few pixels against neighbouring elements.

Where seen: rendered flyers reviewed 2026-08-10.
Impact: subtle, but it is the difference between "designed" and "assembled".

## 5. Template filenames must match the catalog exactly — Open, and it will
break a run

Not a visual defect, but it belongs to whoever maintains the Drive folder.

Gable picks a design by matching the Drive filename against
`src/gable/slides/catalog.py`, in the form `Category — Label`, and the
separator is an **em dash** (—), not a hyphen (-). Files must also carry the
Drive app property `gable_role=template`.

A hyphen where an em dash should be, or a missing app property, produces "I do
not have a design filed for that yet" and stops the run. If a template is
renamed in Drive, `catalog.py` has to be updated in the same change.

---

## How to report a new one

Add a numbered section with: what is wrong, which design and where on it, and
what it should be instead. Attach or name the rendered example. If it is
something Gable is producing rather than something the template contains, it is
a code bug, not a template issue — file it in `STATUS.md` instead.
