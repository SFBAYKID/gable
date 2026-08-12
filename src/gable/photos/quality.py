"""Telling Carmen a photo is too small, before the flyer goes out.

`fit.assess` already works out how far a source has to be enlarged to fill a
frame. Nothing ever showed that number to anyone, so photos were quietly
upscaled and the softness only became visible on the finished flyer — by which
point the fix is to ask the agent for a better picture and rebuild.

Every test photo used on 2026-08-12 was under the minimum: 266x189 through
738x414 against a frame that wants 1078x504. They filled the frame and they
looked soft, and Gable said nothing.

The thresholds below are ratios rather than pixel counts on purpose. Frames
differ by design — measured across the deck they run from 0.55 to 2.17 in
aspect — so "at least 1100 pixels wide" is right for one template and wrong for
the next. What travels is how far the source has to stretch.

Does not handle: judging whether the photograph is any good. Focus, exposure and
whether it is even the right house are Carmen's call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from gable.photos.fit import FitAssessment

#: Enlarging by less than this is invisible on a flyer.
CLEAN_UPSCALE: Final[float] = 1.05

#: Up to this, softness is noticeable to a designer but the flyer is usable.
#: Set from a real case: 738x414 into this frame is 1.46x and visibly soft on an
#: eleven-inch-wide photo, so the line sits below it rather than above.
SOFT_UPSCALE: Final[float] = 1.4

#: Beyond this the result looks obviously blurred at flyer size. Every photo
#: submitted in testing landed here.
POOR_UPSCALE: Final[float] = 2.0

#: Cropping away more than this much of a photo usually means the shape is
#: wrong for the frame, and the subject is likely to lose its head or its roof.
HEAVY_CROP: Final[float] = 0.45


class Grade(Enum):
    """How usable a photo is at the size it will be printed."""

    GOOD = "good"
    SOFT = "soft"
    POOR = "poor"


@dataclass(frozen=True, slots=True)
class Verdict:
    """What to do about a photo, and what to say about it."""

    grade: Grade
    upscale: float
    crop_loss: float
    #: What Carmen reads. Empty when there is nothing worth saying.
    say: str

    @property
    def should_warn(self) -> bool:
        """True when Carmen should be told before this reaches a client."""
        return self.grade is not Grade.GOOD

    @property
    def blocks_delivery(self) -> bool:
        """False, always — a soft photo is Carmen's judgement, not Gable's.

        Refusing to build would leave her with nothing to look at and no way to
        decide. She gets the flyer and the warning together, and decides whether
        to ask the agent for a better picture.
        """
        return False


def _pixels_wanted(assessment: FitAssessment) -> tuple[int, int]:
    """The source size that would need no enlargement at all."""
    return assessment.target_width, assessment.target_height


def judge(assessment: FitAssessment) -> Verdict:
    """Decide whether a photo is big enough, and say so in plain words.

    Args:
        assessment: The result of `fit.assess` for this photo and frame.

    Returns:
        A `Verdict`. `say` is empty when the photo is fine, so a caller can post
        it unconditionally and stay quiet in the common case.

    Raises:
        Nothing.
    """
    upscale = assessment.upscale_factor
    crop = assessment.crop_loss
    want_w, want_h = _pixels_wanted(assessment)
    have = f"{assessment.source_width}x{assessment.source_height}"

    if upscale >= POOR_UPSCALE:
        grade = Grade.POOR
        say = (
            f"That photo is {have}, and this design needs about {want_w}x{want_h}. "
            f"I can use it, but enlarging it {upscale:.1f} times will look blurred "
            "on the finished flyer. A bigger version would be much better if the "
            "agent has one."
        )
    elif upscale >= SOFT_UPSCALE:
        grade = Grade.SOFT
        say = (
            f"That photo is {have} and needs enlarging {upscale:.1f} times to fill "
            f"the space, so it will look a little soft. It is usable — worth asking "
            "for a larger one if it matters for this listing."
        )
    elif crop >= HEAVY_CROP:
        grade = Grade.SOFT
        say = (
            f"The photo is a different shape from the space on this design, so about "
            f"{crop * 100:.0f}% of it gets cropped away. Worth a look to check the "
            "house is still framed the way you want."
        )
    else:
        grade = Grade.GOOD
        say = ""

    return Verdict(grade=grade, upscale=upscale, crop_loss=crop, say=say)


def wanted_size(assessment: FitAssessment) -> str:
    """One line describing the ideal photo for this frame.

    Args:
        assessment: Any assessment against the frame in question.

    Returns:
        A sentence naming the pixel size and shape to aim for, for answering
        "what size should the photo be?" without anyone doing the arithmetic.

    Raises:
        Nothing.
    """
    width, height = _pixels_wanted(assessment)
    ratio = width / height if height else 0.0
    shape = "landscape" if ratio > 1.2 else ("portrait" if ratio < 0.85 else "square-ish")
    return (
        f"At least {width}x{height} pixels, {shape}, roughly {ratio:.1f} to 1. "
        f"Larger is fine — I crop from the centre, so keep the house in the middle."
    )
