"""Fitting a supplied photo to a template's hero frame.

The important idea here is a cost one: **most of this needs no AI at all.**

Cropping and resizing an image to a target aspect ratio is deterministic. Pillow
does it locally, in milliseconds, for nothing. An image model is only needed when
pixels must be *invented* — when the source is smaller than the frame and
enlarging it would visibly smear. So the decision this module makes is not "how
do I fix this photo" but "does this photo need a model at all", and the answer is
usually no.

`assess` is pure and answers that question. `fit_locally` performs the free path.
Neither touches the network.

Does not handle: the generative upscale itself (that is `photos/enhance.py`, and
it is policy-gated), uploading anywhere, or deciding *which* photo to use.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from PIL import Image, ImageOps

#: Below this, enlarging is visible as softness on a printed flyer. A 1080-wide
#: frame filled from a 540-wide source is a 2x enlargement, which is the most a
#: bicubic resample carries without looking obviously soft.
#: ASSUMPTION: 2x is the tolerable limit. Confirmed by rendering one and looking
#: at it — see the vision pass in ARCHITECTURE.md §4.7b, which is the backstop.
MAX_TOLERABLE_UPSCALE: Final[float] = 2.0

#: Aspect ratios closer than this are treated as equal, so a 1079x1350 photo is
#: not sent round a crop path for a rounding error.
ASPECT_EPSILON: Final[float] = 0.01

#: JPEG quality for the fitted output. 88 is visually lossless at flyer sizes
#: and roughly half the bytes of 95.
OUTPUT_QUALITY: Final[int] = 88


class FitAction(StrEnum):
    """What has to happen to make a photo sit correctly in the frame."""

    #: Already the right shape and big enough. Use as-is.
    USE_AS_IS = "use_as_is"
    #: Right shape, too big. Downscale only — always free and always safe.
    DOWNSCALE = "downscale"
    #: Wrong shape, enough pixels. Crop to the frame — free.
    CROP = "crop"
    #: Too few pixels. Enlarging would smear, so a model must invent detail.
    NEEDS_UPSCALE = "needs_upscale"


@dataclass(frozen=True, slots=True)
class FitAssessment:
    """What `assess` concluded, and why. Pure data, safe to log."""

    action: FitAction
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    #: How much the source would have to be enlarged to fill the frame. Below
    #: 1.0 means the source is larger than needed, which is the happy case.
    upscale_factor: float
    #: Fraction of the source that cropping to the frame would discard.
    crop_loss: float
    #: One sentence, written for Carmen rather than for a log parser.
    reason: str

    @property
    def needs_model(self) -> bool:
        """True only when a paid image call is genuinely unavoidable."""
        return self.action is FitAction.NEEDS_UPSCALE

    @property
    def is_free(self) -> bool:
        """True when Pillow alone can do it, at no cost and no latency."""
        return not self.needs_model


def assess(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> FitAssessment:
    """Decide how a photo must be changed to fill a frame, and whether it is free.

    Args:
        source_width: Supplied photo width in pixels. Must be positive.
        source_height: Supplied photo height in pixels. Must be positive.
        target_width: Hero frame width in pixels. Must be positive.
        target_height: Hero frame height in pixels. Must be positive.

    Returns:
        A `FitAssessment`. Check `needs_model` before spending anything.

    Raises:
        ValueError: if any dimension is not positive. A zero dimension is a
            corrupt image or a bad frame, and guessing past it would put a
            stretched photo on a flyer.
    """
    for name, value in (
        ("source_width", source_width),
        ("source_height", source_height),
        ("target_width", target_width),
        ("target_height", target_height),
    ):
        if value <= 0:
            msg = f"{name} must be positive, got {value}"
            raise ValueError(msg)

    source_aspect = source_width / source_height
    target_aspect = target_width / target_height

    # To COVER the frame, both axes must reach it; the binding constraint is
    # whichever needs enlarging more.
    upscale = max(target_width / source_width, target_height / source_height)

    # Cropping to the frame's aspect discards one axis. Work out how much.
    if abs(source_aspect - target_aspect) <= ASPECT_EPSILON:
        crop_loss = 0.0
    elif source_aspect > target_aspect:
        # Source is wider: the sides go.
        crop_loss = 1.0 - (target_aspect / source_aspect)
    else:
        # Source is taller: top and bottom go.
        crop_loss = 1.0 - (source_aspect / target_aspect)

    if upscale > MAX_TOLERABLE_UPSCALE:
        action = FitAction.NEEDS_UPSCALE
        reason = (
            f"the photo is {source_width}x{source_height}, which would have to be "
            f"enlarged {upscale:.1f}x to fill a {target_width}x{target_height} frame. "
            f"Past {MAX_TOLERABLE_UPSCALE:.0f}x that looks soft, so this one needs a model."
        )
    elif abs(source_aspect - target_aspect) <= ASPECT_EPSILON:
        if upscale >= 1.0:
            action = FitAction.USE_AS_IS
            reason = "already the right shape and large enough; using it unchanged."
        else:
            action = FitAction.DOWNSCALE
            reason = (
                f"right shape, and larger than needed — downscaling "
                f"{1 / upscale:.1f}x, which is free and lossless to the eye."
            )
    else:
        action = FitAction.CROP
        orientation = "wider" if source_aspect > target_aspect else "taller"
        reason = (
            f"the photo is {orientation} than the frame, so {crop_loss:.0%} of it "
            f"is cropped away. No model needed."
        )

    return FitAssessment(
        action=action,
        source_width=source_width,
        source_height=source_height,
        target_width=target_width,
        target_height=target_height,
        upscale_factor=upscale,
        crop_loss=crop_loss,
        reason=reason,
    )


def fit_locally(
    image_bytes: bytes,
    target_width: int,
    target_height: int,
    quality: int = OUTPUT_QUALITY,
) -> bytes:
    """Crop and scale a photo to exactly fill the frame. No network, no cost.

    Centre-crops to the frame's aspect ratio, then resamples to the exact target
    size. The centre crop is deliberate: on a listing photo the house is almost
    always centred, and cropping from an edge is how you decapitate a roofline.

    Args:
        image_bytes: The source image, in any format Pillow reads.
        target_width: Frame width in pixels.
        target_height: Frame height in pixels.
        quality: JPEG quality for the output.

    Returns:
        JPEG bytes at exactly `target_width` x `target_height`.

    Raises:
        ValueError: if a target dimension is not positive.
        OSError: if Pillow cannot read `image_bytes`. Left to propagate because
            an unreadable upload is worth a specific message to Carmen, not a
            silent fallback to a stretched image.
    """
    if target_width <= 0 or target_height <= 0:
        msg = f"target must be positive, got {target_width}x{target_height}"
        raise ValueError(msg)

    with Image.open(io.BytesIO(image_bytes)) as opened:
        # Apply EXIF orientation FIRST. A portrait photo from a phone is stored
        # landscape with an orientation tag, so without this the crop runs along
        # the wrong axis: a 4000x3000-stored portrait shot would be trimmed 40%
        # off its sides instead of 6% off top and bottom, and ship sideways.
        upright = ImageOps.exif_transpose(opened)
        # Flyers are JPEG; a palette or alpha image must be flattened first or
        # Pillow refuses to save, and transparency would show as black anyway.
        im: Image.Image = upright.convert("RGB")

        source_aspect = im.width / im.height
        target_aspect = target_width / target_height

        if source_aspect > target_aspect:
            # Too wide: trim the sides, keep full height.
            new_width = round(im.height * target_aspect)
            left = (im.width - new_width) // 2
            box = (left, 0, left + new_width, im.height)
        else:
            # Too tall: trim top and bottom, keep full width.
            new_height = round(im.width / target_aspect)
            top = (im.height - new_height) // 2
            box = (0, top, im.width, top + new_height)

        cropped = im.crop(box)
        resized = cropped.resize((target_width, target_height), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        resized.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


def image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Read an image's pixel dimensions without decoding the whole frame.

    Args:
        image_bytes: The image.

    Returns:
        `(width, height)`.

    Raises:
        OSError: if Pillow cannot identify the format.
    """
    with Image.open(io.BytesIO(image_bytes)) as im:
        # As displayed, not as stored — see `fit_locally`. `assess` compares
        # these against the frame, so reporting stored dimensions for a rotated
        # photo makes it choose the wrong crop axis.
        upright = ImageOps.exif_transpose(im)
        return upright.width, upright.height
