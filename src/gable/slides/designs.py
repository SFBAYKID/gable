"""What is known about Carmen's six designs, recorded per design.

`hero.py` measures; this file remembers. Each entry here was established against
that template's own render rather than read by eye, and every one of them exists
because a geometric rule alone chose wrong on a real flyer.

Does not handle: measurement, placement, or anything about a design not in the
Generic Templates folder — an unrecorded name falls through to the geometric
search, which refuses rather than guesses.
"""

from __future__ import annotations

from typing import Any, Final

#: The photo well for each Carmen-maintained design, where the geometric search
#: cannot choose. Every PPTX import carries a second unfilled, untexted shape
#: overlapping the photo band, so `find_hero_frame` sees two candidates and
#: correctly refuses. The second shape is *not* disposable: deleting Sold's
#: removed the white panel behind the Corner House logo and left it washed out
#: over the brickwork.
#:
#: An earlier generation of hand-read ids was abandoned because one of three was
#: wrong. These are different in the ways that caused that: there are six
#: designs rather than forty-five, and each id was measured against that
#: template's own render rather than read by eye — the rejected candidate in
#: four cases contains 9-20% near-white pixels because it also covers the logo
#: strip, while the chosen one is pure photograph.
#:
#: These name the shape to REPLACE — the one carrying the design's sample
#: photograph. Where the new image is drawn can be a smaller rectangle inside
#: it; see `_placement_guide`.
#:
#: This is a hint, never an authority. `find_hero_frame` re-measures the named
#: shape and falls back to the geometric search when it is absent or implausible,
#: so a redesigned template degrades to "ask" rather than to a wrong frame.
HERO_OBJECT_IDS: Final[dict[str, str]] = {
    "sold": "p1_i87",
    "under contract": "p1_i88",
    # Corrected 2026-08-14: `p1_i104` is a sky backdrop, and the sample house
    # is attached to `p1_i105` in front of it, so replacing `p1_i104` put the
    # supplied photo behind the template's own house and the visual gate
    # reported a completely different property. Established by copying the
    # design and deleting each in turn. `p1_i105` runs up behind the logo, and
    # `p1_i104` is contained within it and starts lower, so the placement guide
    # below draws the photo in exactly the band the design shows.
    "open house": "p1_i105",
    "new listing": "p1_i92",
    "new listing with open house": "p1_i92",
    "client review post": "p1_i90",
}

#: Picture-bearing layers that must be deleted ALONGSIDE the well, because the
#: design's sample photograph is split across more than one shape and removing
#: only the well leaves the rest of it showing.
#:
#: New Listing is the case that proved it: its photograph is two overlapping
#: shapes, `p1_i90` covering the top of the slide behind the logo and `p1_i92`
#: the band below. Replacing only `p1_i92` left the template's own brick mansion
#: above the new photo — two houses stacked on one flyer, which is exactly what
#: Chase saw. Established by copying the design and deleting each shape in turn:
#: deleting `p1_i90` alone leaves a clean logo band and the full sample below,
#: deleting `p1_i92` alone leaves the sample above and a white gap beneath.
#:
#: This is per design and evidence-only. Sold's second shape is deliberately
#: absent from this map: it is the white panel behind the Corner House logo, and
#: deleting it once left the logo washed out over the brickwork.
HERO_EXTRA_DELETE: Final[dict[str, tuple[str, ...]]] = {
    "new listing": ("p1_i90",),
    # Same shape as New Listing: `p1_i92` is the band the design shows and is
    # where the photo belongs, while `p1_i93` carries the sample house on top of
    # it. Replacing only `p1_i92` put the supplied photo behind the template's
    # own brick colonial. Deleting `p1_i93` alone leaves the sunset backdrop in
    # exactly that band, which is what the source renders.
    "new listing with open house": ("p1_i93",),
}


def extra_deletions(page: dict[str, Any], template_label: str, well_id: str) -> tuple[str, ...]:
    """Return the other sample-photo layers this design needs removed.

    Args:
        page: A `slides[n]` entry from a presentations.get response.
        template_label: The design's name.
        well_id: The shape already being replaced, never returned again.

    Returns:
        Object ids present on the page, in the order recorded. Empty for every
        design with a single photo layer, which is five of the six.

    Raises:
        Nothing.
    """
    recorded = HERO_EXTRA_DELETE.get(" ".join(template_label.split()).casefold(), ())
    if not recorded:
        return ()
    present = {
        str(element.get("objectId", ""))
        for element in page.get("pageElements", [])
        if "elementGroup" not in element
    }
    return tuple(name for name in recorded if name in present and name != well_id)
