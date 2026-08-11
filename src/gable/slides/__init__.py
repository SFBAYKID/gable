"""Google Slides rendering: the assembly engine.

Replaces the Canva Bulk Create path, which Spike A proved cannot carry a photo
from an uploaded file (`spikes/SPIKE_A_RESULT.md`). Slides can: `replaceAllText`
fills the copy and `replaceAllShapesWithImage` pulls the hero photo in from a
public URL, and the result is a live document Carmen can open and edit herself.

Carmen still designs in Canva. She exports the static frame as a PNG; that PNG
is the slide background and only the changing parts sit on top of it.
"""

from __future__ import annotations
