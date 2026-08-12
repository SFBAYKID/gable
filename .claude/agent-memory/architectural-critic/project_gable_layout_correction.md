---
name: gable-layout-correction
description: Why automatic layout correction of Carmen's templates was rejected in review on 2026-08-11 — the Slides API measures boxes, not ink, and per-template offsets die on re-export.
metadata:
  type: project
---

A three-layer "measure geometry -> auto-correct transforms -> re-render" design for fixing
Chase's recurring layout feedback was reviewed on 2026-08-11 and **layer 2 (the corrector) was
rejected**; layer 1 (measure and report) was approved only as a reporter.

**Why:**

1. **Slides exposes box geometry, never ink geometry.** `absolute_boxes` in
   `src/gable/slides/hero.py` composes element transforms into slide coordinates exactly. It
   still cannot see where a glyph lands *inside* a shape. `presentations.get` returns transform,
   size, paragraph style and text style — no rendered glyph bounds, and `TextStyle` has no
   tracking/letter-spacing field at all. Nearly all layout feedback ("evenly spaced", "aligned on
   the same line", "too far apart", "centered in the box") is about ink. Equal box `y` is not
   equal baseline once font sizes or `contentAlignment` differ; equal box gaps are not equal
   visual gaps once a box is wider than its text. A checker built on box geometry measures the
   wrong quantity *precisely*, which produces confident wrong corrections.
2. **The one thing that is exact is already built and is not geometry.** `set_alignment`
   (`updateParagraphStyle`, CENTER) and `set_content_alignment` (`updateShapeProperties`,
   MIDDLE) in `src/gable/slides/edits.py` are idempotent and need no measurement. "Center it in
   the gray box" is those two calls, not a transform.
3. **Per-template correction state cannot survive Carmen.** `src/gable/slides/manifest.py`
   already is a per-template store (`hero_object_id`), and only 3 of 45 are populated. Object ids
   are regenerated on every PPTX re-export, and `BRAND.md` records that Carmen is still editing
   the 69-page master the 45 templates are exported from. Any stored per-template offset is
   invalidated by the next export, silently.
4. **`TEMPLATE_ISSUES.md` #4 (panel misalignment) is a binding out-of-scope instruction from
   Chase**: "do not attempt to work around them in code." A corrector that nudges panels onto a
   grid is exactly that workaround.
5. **Re-applying a correction double-applies it.** `move_element` in
   `src/gable/slides/geometry.py` is deliberately RELATIVE. A retried or restarted run re-runs
   the correction and moves the element twice.

**How to apply:** when any future plan proposes measuring or auto-fixing layout, first ask which
quantity it measures — box or ink. If it needs ink, the only honest surface is the rendered
`getThumbnail` PNG (already fetched in `src/gable/pipeline/live.py`), analysed deterministically
with Pillow/numpy and attributed back to elements using `absolute_boxes` regions. A vision model
is not a substitute: `STATUS.md` records overlap counts of 6, 11, 8, 13 across four identical
runs, and that non-determinism is a property of the approach, not of the vendor.

Related: [[gable-decision-discipline]], [[gable-unwired-seams]]
