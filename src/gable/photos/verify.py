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
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Final

from PIL import Image

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

#: A seam must be at least this many grey levels (0-255) of row-to-row jump.
#: Calibrated 2026-08-11 against real photos rather than guessed.
_MIN_SEAM_JUMP: Final[float] = 6.0

#: Grid the seam check samples on. Coarse enough to be cheap, fine enough that
#: a one-pixel paste line still lands on its own row.
_SEAM_COLS: Final[int] = 64
_SEAM_ROWS: Final[int] = 128

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
    except urllib.error.HTTPError as exc:
        return Verdict(ok=False, say=f"that image link came back as unavailable ({exc.code})")
    except Exception:
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
    except Exception:
        return Verdict(
            ok=False,
            say="that file says it is an image but I could not read it",
            content_type=content_type,
        )

    if min(width, height) < MIN_EDGE_PX:
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


def seam_score(image_bytes: bytes) -> float:
    """How likely an image has a hard horizontal seam across it.

    A failed sky replacement leaves a straight line where the new sky was
    pasted in. One reached a flyer, which is why this exists.

    **This is a weak signal and must not be used as a hard gate.** Measured
    2026-08-11 against real photographs: a deliberately seamed image scored
    1.83 on the underlying ratio and the same image unseamed scored 1.79. Two
    percent apart. Ordinary listing photos are full of full-width horizontal
    transitions — a treeline, a roofline, a kerb, the edge of a lawn — and at
    any sampling resolution cheap enough to run inline, those are the same
    measurement as a pasted sky.

    Three designs were tried and all three failed the same way; the earlier two
    are described in the body below. Use this to *rank* or to log, and let the
    vision pass over the rendered flyer be the thing that actually decides.
    `enhance.composition_distance` is the reliable numeric check — it catches
    the failure that matters, which is the model returning a different house.

    Args:
        image_bytes: The image to check.

    Returns:
        0.0 for no detectable seam, rising with confidence, capped at 1.0.
        Treat anything under roughly 0.8 as uninformative rather than as a pass.

    Raises:
        Nothing.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            grey = opened.convert("L").resize((_SEAM_COLS, _SEAM_ROWS))
            pixels = [
                [float(grey.getpixel((x, y))) for x in range(_SEAM_COLS)]  # type: ignore[arg-type]
                for y in range(_SEAM_ROWS)
            ]
    except Exception:
        return 0.0

    # What separates a pasted sky from a roofline is UNIFORMITY, not size.
    #
    # Two earlier versions of this compared row-mean brightness and both were
    # wrong, verified against real photos on 2026-08-11. Comparing the biggest
    # row jump to the median made a clean gradient sky score 1.0, because its
    # median is near zero. Adding an absolute floor did not help either: the
    # roofline of a house is a genuine, large, horizontal brightness step, so
    # every ordinary listing photo still scored 1.0 and the quality gate
    # rejected every enhancement — including a plain resize with no model in it.
    #
    # A failed sky replacement shifts EVERY column by nearly the same amount,
    # so the per-column deltas cluster tightly around their mean. A roofline
    # shifts some columns hugely (sky to shingle) and others not at all (sky to
    # sky), so the same deltas are spread wide. Comparing the mean jump to its
    # own spread separates them; comparing magnitudes never can.
    best = 0.0
    for y in range(1, _SEAM_ROWS - 1):
        deltas = [pixels[y + 1][x] - pixels[y][x] for x in range(_SEAM_COLS)]
        mean = sum(deltas) / _SEAM_COLS
        if abs(mean) < _MIN_SEAM_JUMP:
            continue
        variance = sum((d - mean) ** 2 for d in deltas) / _SEAM_COLS
        # The +1 keeps a perfectly flat region from dividing by zero and also
        # stops a trivially small jump from scoring high on tidiness alone.
        best = max(best, abs(mean) / (variance**0.5 + 1.0))

    # Below ~1.0 the spread swamps the jump, which is an edge, not a seam.
    return min(1.0, max(0.0, (best - 1.0) / 3.0))
