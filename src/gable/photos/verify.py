"""Checking an image before it is put on a flyer.

A rendered flyer arrived with the template's own background illustration sitting
inside the agent headshot frame. The URL was accepted because nothing looked at
what was behind it — it was a string that ended in `.jpg`, and that was the whole
check.

So every image URL is now fetched and inspected before it is emitted:

* it must answer **200**
* its **content type** must be an image format Slides accepts
* its **dimensions** must be plausible for the slot — a headshot is roughly
  square, a hero photo is not

A mismatch is rejected rather than passed through, because the failure it
produces is silent: every API call still succeeds and the flyer simply looks
wrong.
"""

from __future__ import annotations

import io
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Final

from PIL import Image

logger = logging.getLogger("gable.photos.verify")

#: What Slides will fetch and render.
ALLOWED_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/gif"}
)

#: Aspect bounds per slot, as width divided by height.
ASPECT_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "square": (0.75, 1.35),  # a headshot; allows a slightly tall portrait crop
    "landscape": (1.05, 2.60),  # a hero photo lying on its side
    "portrait": (0.55, 0.95),  # a hero photo for a tall frame
    "any": (0.05, 20.0),
}

#: Below this the image is too small to print without visible softness.
MIN_EDGE_PX: Final[int] = 200

_TIMEOUT_SECONDS: Final[int] = 30
#: Enough to identify format and size without pulling a whole photo.
_HEADER_BYTES: Final[int] = 65536


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether an image may be used, and why not if not."""

    ok: bool
    #: Plain words, safe to post. Empty when the image is fine.
    say: str = ""
    width: int = 0
    height: int = 0
    content_type: str = ""

    @property
    def aspect(self) -> float:
        """Width divided by height, or 0 when unknown."""
        return self.width / self.height if self.height else 0.0


def verify(url: str, slot: str = "any", timeout: int = _TIMEOUT_SECONDS) -> Verdict:
    """Fetch an image and decide whether it belongs in a slot.

    Args:
        url: The image URL about to be emitted.
        slot: `square`, `landscape`, `portrait`, or `any`.
        timeout: Seconds to wait.

    Returns:
        A `Verdict`. Never raises: this runs on the render path, and the right
        response to an unreachable image is to say so, not to crash.

    Raises:
        Nothing.
    """
    if not url.strip():
        return Verdict(ok=False, say="I do not have an image for that slot.")

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Gable/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            head = response.read(_HEADER_BYTES)
    except urllib.error.HTTPError as exc:  # silent: the verdict carries the reason to the caller
        del exc
        return Verdict(ok=False, say="that image link came back as unavailable")
    except Exception as error:
        logger.warning("the image link could not be fetched: %s", type(error).__name__)
        return Verdict(ok=False, say="I could not reach that image link")

    if status != 200:
        return Verdict(ok=False, say="that image link did not load")

    if content_type not in ALLOWED_TYPES:
        return Verdict(
            ok=False,
            say="that link is not an image Google Slides can use",
            content_type=content_type,
        )

    try:
        with Image.open(io.BytesIO(head)) as opened:
            width, height = opened.size
    except Exception as error:
        logger.warning("the image could not be decoded: %s", type(error).__name__)
        return Verdict(
            ok=False,
            say="that file says it is an image but I could not read it",
            content_type=content_type,
        )

    # An aspect-preserved human upload is checked as ``any`` before its actual
    # template frame is known. A small source is still usable: the deterministic
    # contained fit preserves it over a same-photo backdrop. AGENTS.md forbids
    # asking for a larger copy merely because the upload is small. Slot-specific
    # assets such as headshots keep the minimum because they do not use that fit.
    if slot != "any" and min(width, height) < MIN_EDGE_PX:
        return Verdict(
            ok=False,
            say=f"that image is only {width} by {height}, too small to print cleanly",
            width=width,
            height=height,
            content_type=content_type,
        )

    low, high = ASPECT_BOUNDS.get(slot, ASPECT_BOUNDS["any"])
    aspect = width / height
    if not (low <= aspect <= high):
        shape = "square-ish" if slot == "square" else slot
        return Verdict(
            ok=False,
            say=(
                f"that image is {width} by {height}, which is the wrong shape for a "
                f"{shape} slot — it would be cropped badly"
            ),
            width=width,
            height=height,
            content_type=content_type,
        )

    return Verdict(ok=True, width=width, height=height, content_type=content_type)
