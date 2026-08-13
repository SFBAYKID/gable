"""Google Slides rendering: the assembly engine.

Replaces the Canva Bulk Create path, which Spike A proved cannot carry a photo
from an uploaded file (`spikes/SPIKE_A_RESULT.md`). Slides can: `replaceAllText`
fills standalone fields, then Gable deletes the exactly measured hero-frame
object and inserts the fitted photo at those bounds with `createImage`. The
result is a live document Carmen can open and edit herself.
"""

from __future__ import annotations
