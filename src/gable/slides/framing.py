"""Pulling a measured frame back off whatever the design draws beside it.

These designs place the agent as a transparent cut-out, so a frame's rectangle
runs into things the person never covers: New Listing's reaches 16pt into the
footer band, and New Listing with Open House's reaches across the start of
REALTOR. A photograph is opaque to its edges, so it painted over both, and the
visual gate reported each one.

Pure rectangle arithmetic, deliberately: it takes plain bounds and returns plain
bounds, so `hero.py` keeps sole ownership of what a frame means and this stays
testable without a slide.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

#: (x, y, width, height) in EMU.
Bounds = tuple[float, float, float, float]

#: Never clip a frame below this fraction of its measured area. Past it the
#: design is not "a cut-out with room around it" any more, and guessing at a
#: much smaller portrait is worse than the overlap the clip was avoiding.
MIN_KEPT_AREA: Final[float] = 0.70


def _area(bounds: Bounds) -> float:
    """The rectangle's area, or zero when it has been clipped away."""
    return max(0.0, bounds[2]) * max(0.0, bounds[3])


def clear_of_neighbours(frame: Bounds, neighbours: Iterable[Bounds]) -> Bounds:
    """Pull a frame back off every neighbour it only partly overlaps.

    Args:
        frame: The measured well.
        neighbours: Every other drawn element's bounds on the same slide.

    Returns:
        The frame, pulled back on whichever single side costs least for each
        neighbour in turn. Unchanged where nothing partly overlaps it, where the
        neighbour contains it — a background is not something to avoid — or
        where the clip would take more of the frame than `MIN_KEPT_AREA` allows.

    Raises:
        Nothing.
    """
    x, y, width, height = frame
    for nx, ny, nwidth, nheight in neighbours:
        if nwidth <= 0 or nheight <= 0:
            continue
        if min(x + width, nx + nwidth) - max(x, nx) <= 0:
            continue
        if min(y + height, ny + nheight) - max(y, ny) <= 0:
            continue
        # A background the frame sits inside is not a neighbour to avoid.
        if nx <= x and ny <= y and nx + nwidth >= x + width and ny + nheight >= y + height:
            continue
        options = (
            (x, y, width, ny - y),
            (x, ny + nheight, width, y + height - (ny + nheight)),
            (x, y, nx - x, height),
            (nx + nwidth, y, x + width - (nx + nwidth), height),
        )
        usable = [option for option in options if option[2] > 0 and option[3] > 0]
        if not usable:
            continue
        best = max(usable, key=_area)
        if _area(best) < _area((x, y, width, height)) * MIN_KEPT_AREA:
            continue
        x, y, width, height = best
    return x, y, width, height
