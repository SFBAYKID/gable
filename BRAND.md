# Corner House Realty — the brand facts

Read from Carmen's own brand board, page 1 of the master Canva design
"Corner House Static · Instagram Post (4:5)", on 2026-08-11. These are not
inferred from the exported templates; they are the source of truth she designs
against.

## Typefaces

| Face | Used for |
|---|---|
| **Open Sans** | Headlines and stats. The bold weight (700) is what prices and agent names are set in. |
| **EB Garamond** | Serif copy — "Sold For", "Thinking of Selling?", the footer line. |

This matters beyond documentation. `slides/fitting.py` estimates text width from
a five-bucket character table, and that table was calibrated against exactly
these two faces — which until now was a claim in a comment. It is now confirmed
from the brand board, so the estimator is calibrated against the right thing and
the remaining error is weight, which is read per run.

## Colours

| Hex | Role |
|---|---|
| `#859A9B` | Sage. Price bands, "Sold" wordmark, dividers. |
| `#FFFFFF` | White. |
| `#CACACA` | Light grey. Panels behind copy. |
| `#070A4C` | Navy. Headlines, the footer bar, "OPEN HOUSE". |

Any edit that recolours something should choose from these four. A request like
"make that darker" means `#070A4C`, not an arbitrary darker shade.

## Format

**Instagram post, 4:5.** The master design is authored at that ratio, which is
where the 1080x1350 render target comes from. Every hero photo is fitted to
exactly that, proved across nine source shapes.

## The master design

69 pages, held in Carmen's Canva design, still being finalised — she said as
much in Slack on 2026-08-10: "I'm working on editing the templates and will have
final versions finished tomorrow."

**The 45 templates in the Gable shared drive are an export of a subset of it.**
That is worth stating plainly, because it explains several defects logged in
`TEMPLATE_ISSUES.md`: the "approch" typo, the low-contrast logo and the panel
misalignments are in the source design, so they cannot be fixed by re-exporting
alone and they will come back if the export is repeated before Carmen fixes
them.

## Still needed

A PDF export of all 69 pages, to see each design **filled in correctly**. The
Gable drive holds the blank templates only, so every judgement about what a
finished flyer should look like is currently inference. With the export it
becomes comparison.

Specifically it would settle:

* which blanks each design expects to be filled, including ones Gable has not
  learned — the way `@reallygreatsite` and `REALTOR / TITLE` only surfaced when
  a rendered flyer exposed them
* what a two-agent design looks like when both agents are real people
* whether the decorative overlaps the vision pass objects to are deliberate
