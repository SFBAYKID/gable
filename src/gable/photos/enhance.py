"""High-fidelity upscaling of a supplied REAL property photo.

This is deliberately an image *edit*, never generation. The source image is
first centre-fitted to the exact flyer composition. The model may restore detail
lost to a low-resolution upload, but the prompt forbids changing the property,
scene, geometry, words, or composition. A coarse composition comparison rejects
an output that drifted too far from the supplied photograph.

The caller owns policy, spend, and per-listing call limits. Keeping those out of
this provider function makes the paid boundary injectable and unit-testable.
"""

from __future__ import annotations

import base64
import binascii
import io
import math
from collections.abc import Callable, Mapping
from typing import Any, Final, cast

import httpx
from PIL import Image

from gable.photos.fit import assess, fit_locally, image_dimensions

_ENDPOINT: Final[str] = "https://api.openai.com/v1/images/edits"
_TIMEOUT_SECONDS: Final[float] = 180.0
_MAX_OUTPUT_BYTES: Final[int] = 32 * 1024 * 1024
_GPT_IMAGE_2_EDGE_STEP: Final[int] = 16
_GPT_IMAGE_2_MIN_PIXELS: Final[int] = 655_360
_GPT_IMAGE_2_MAX_PIXELS: Final[int] = 8_294_400
_GPT_IMAGE_2_MAX_EDGE: Final[int] = 3_840
_GPT_IMAGE_2_MAX_ASPECT: Final[float] = 3.0
# ASSUMPTION: 0.18 separates sharpening from a materially changed composition.
# Confirm by comparing the real rejected and accepted upscales during the first
# watched Slack workflow; STATUS.md keeps that visual certification open.
_MAX_COMPOSITION_DISTANCE: Final[float] = 0.18

# OpenAI's image-edit prompting guidance says to name both the one allowed
# change and every invariant. The factual-preservation language matters here:
# this is a photograph of a specific property, not a style reference.
_PROMPT: Final[str] = """Task: super-resolution only.
Increase the resolution and restore natural photographic detail in this exact
real-estate listing photo so it remains sharp in a social-media flyer.

Change only resolution and sharpness. Preserve the exact property, building
geometry, roofline, windows, doors, address numbers, landscaping, sky, lighting,
camera viewpoint, crop, colors, people, vehicles, signs, logos, and every piece
of visible text. Do not add, remove, replace, beautify, redesign, relight, or
recompose anything. This must remain a truthful photograph of the same specific
property, with the same composition edge to edge.
"""

PostImageEdit = Callable[
    [str, Mapping[str, str], Mapping[str, str], Mapping[str, tuple[str, bytes, str]], float],
    httpx.Response,
]


class EnhancementError(Exception):
    """A real photo could not be safely enhanced."""


class EnhancementQualityError(EnhancementError):
    """The model returned an image that was not faithful enough to use."""


def _post(
    url: str,
    headers: Mapping[str, str],
    data: Mapping[str, str],
    files: Mapping[str, tuple[str, bytes, str]],
    timeout: float,
) -> httpx.Response:
    """Send one image-edit request through the project's HTTP client."""
    return httpx.post(
        url,
        headers=dict(headers),
        data=dict(data),
        files=dict(files),
        timeout=timeout,
    )


def _gpt_image_2_output_dimensions(
    target_width: int,
    target_height: int,
) -> tuple[int, int]:
    """Find the closest valid GPT Image 2 canvas to a flyer photo frame.

    GPT Image 2 rejects a size unless both edges are multiples of 16, the
    aspect ratio is at most 3:1, and the canvas contains 655,360 to 8,294,400
    pixels with neither edge over 3,840. Small, wide flyer bands are the easy
    constraint to miss: simply rounding 1078x504 to 1088x512 still produces
    too few pixels and returns HTTP 400.

    The continuous target is scaled only as much as the API constraints
    require. Searching the small 240-by-240 output grid then chooses the valid
    integer canvas closest to that target, preserving its aspect ratio without
    accidentally selecting a much larger, more expensive canvas.

    Raises:
        ValueError: If either requested edge is not positive.
    """
    if target_width <= 0 or target_height <= 0:
        msg = f"target must be positive, got {target_width}x{target_height}"
        raise ValueError(msg)

    target_aspect = target_width / target_height
    if target_aspect > _GPT_IMAGE_2_MAX_ASPECT:
        base_width = float(target_width)
        base_height = base_width / _GPT_IMAGE_2_MAX_ASPECT
    elif target_aspect < 1 / _GPT_IMAGE_2_MAX_ASPECT:
        base_height = float(target_height)
        base_width = base_height / _GPT_IMAGE_2_MAX_ASPECT
    else:
        base_width = float(target_width)
        base_height = float(target_height)

    base_pixels = base_width * base_height
    if base_pixels < _GPT_IMAGE_2_MIN_PIXELS:
        scale = math.sqrt(_GPT_IMAGE_2_MIN_PIXELS / base_pixels)
    else:
        scale = min(
            1.0,
            _GPT_IMAGE_2_MAX_EDGE / max(base_width, base_height),
            math.sqrt(_GPT_IMAGE_2_MAX_PIXELS / base_pixels),
        )
    desired_width = base_width * scale
    desired_height = base_height * scale

    # If the requested frame already respects the upper constraints, an output
    # edge must not undershoot it. Otherwise the provider could return a valid
    # canvas that immediately needs another enlargement to fill the frame.
    request_respects_upper_bounds = (
        max(target_width, target_height) <= _GPT_IMAGE_2_MAX_EDGE
        and max(target_aspect, 1 / target_aspect) <= _GPT_IMAGE_2_MAX_ASPECT
        and target_width * target_height <= _GPT_IMAGE_2_MAX_PIXELS
    )

    best: tuple[tuple[float, float, int], int, int] | None = None
    for width in range(
        _GPT_IMAGE_2_EDGE_STEP,
        _GPT_IMAGE_2_MAX_EDGE + 1,
        _GPT_IMAGE_2_EDGE_STEP,
    ):
        if request_respects_upper_bounds and width < target_width:
            continue
        for height in range(
            _GPT_IMAGE_2_EDGE_STEP,
            _GPT_IMAGE_2_MAX_EDGE + 1,
            _GPT_IMAGE_2_EDGE_STEP,
        ):
            if request_respects_upper_bounds and height < target_height:
                continue
            pixels = width * height
            if not _GPT_IMAGE_2_MIN_PIXELS <= pixels <= _GPT_IMAGE_2_MAX_PIXELS:
                continue
            if max(width, height) / min(width, height) > _GPT_IMAGE_2_MAX_ASPECT:
                continue
            distance = (
                ((width / desired_width) - 1) ** 2 + ((height / desired_height) - 1) ** 2,
                abs(pixels - (desired_width * desired_height)),
                pixels,
            )
            candidate = (distance, width, height)
            if best is None or candidate < best:
                best = candidate

    # The documented constraint space always has valid canvases (1024x1024 is
    # one), so reaching this branch means the constants above are inconsistent.
    if best is None:  # pragma: no cover - defensive invariant
        raise EnhancementError("no supported image enlargement size is available")
    return best[1], best[2]


def _accepts_input_fidelity(model: str) -> bool:
    """Whether this model takes an explicit `input_fidelity` on an edit.

    Args:
        model: The configured GPT Image edit model.

    Returns:
        True only for full `gpt-image-1`. The mini variant returns HTTP 400 for
        the field, and `gpt-image-2` is always high fidelity and rejects it.

    Raises:
        Nothing.
    """
    return model.startswith("gpt-image-1") and "mini" not in model


def _output_size(model: str, target_width: int, target_height: int) -> str:
    """Choose a supported portrait, landscape, or square edit size."""
    if model.startswith("gpt-image-2"):
        width, height = _gpt_image_2_output_dimensions(target_width, target_height)
        return f"{width}x{height}"
    if target_width == target_height:
        return "1024x1024"
    if target_width < target_height:
        return "1024x1536"
    return "1536x1024"


def _response_image(response: httpx.Response) -> bytes:
    """Extract and bound the one base64 image returned by the Image API."""
    if response.status_code != 200:
        # Never include the response body: vendor errors can echo request data,
        # and Gable's runtime contract forbids raw provider errors in Slack.
        raise EnhancementError("the image service did not complete the enlargement")
    try:
        payload: Any = response.json()
        encoded = payload["data"][0]["b64_json"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise EnhancementError("the image service returned no usable enlargement") from exc
    if not isinstance(encoded, str) or not encoded:
        raise EnhancementError("the image service returned no usable enlargement")
    if len(encoded) > (_MAX_OUTPUT_BYTES * 4 // 3) + 8:
        raise EnhancementError("the enlarged image exceeded the safe processing limit")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EnhancementError("the image service returned an unreadable enlargement") from exc
    if not decoded or len(decoded) > _MAX_OUTPUT_BYTES:
        raise EnhancementError("the enlarged image exceeded the safe processing limit")
    return decoded


def composition_distance(reference: bytes, candidate: bytes) -> float:
    """Measure large-scale visual drift while ignoring restored fine detail.

    Both images are reduced to a small grayscale grid. A faithful upscale keeps
    those low-frequency structures nearly identical; a newly invented house or
    materially changed composition does not.

    Returns:
        Mean absolute pixel difference from 0.0 (same composition) to 1.0.

    Raises:
        OSError: if either byte string is not a readable image.
    """
    samples: list[list[int]] = []
    for image_bytes in (reference, candidate):
        with Image.open(io.BytesIO(image_bytes)) as opened:
            reduced = opened.convert("L").resize((32, 40), Image.Resampling.LANCZOS)
            samples.append(
                [cast(int, reduced.getpixel((x, y))) for y in range(40) for x in range(32)]
            )
    total_difference = 0
    for left, right in zip(samples[0], samples[1], strict=True):
        total_difference += abs(left - right)
    return total_difference / (len(samples[0]) * 255)


def upscale_real_photo(
    image_bytes: bytes,
    api_key: str,
    model: str,
    target_width: int,
    target_height: int,
    post: PostImageEdit = _post,
) -> bytes:
    """Restore resolution without allowing a supplied property photo to drift.

    Args:
        image_bytes: Carmen's original Slack upload.
        api_key: Existing OpenAI credential. It is sent only in the auth header.
        model: Configured GPT Image edit model.
        target_width: Final flyer width in pixels.
        target_height: Final flyer height in pixels.
        post: Injected HTTP seam used by tests.

    Returns:
        A verified JPEG at exactly the requested flyer dimensions.

    Raises:
        EnhancementError: when the provider fails or returns invalid bytes.
        EnhancementQualityError: when the result changes the composition or
            remains too small for the requested frame.
        OSError: when the original upload is not a readable image.
    """
    if not api_key or not model:
        raise EnhancementError("automatic enlargement is not configured")
    if target_width <= 0 or target_height <= 0:
        raise EnhancementError("the flyer has no usable photo dimensions")

    # Give the model the exact crop it must preserve. This also strips EXIF and
    # bounds the upload sent to the provider, regardless of the phone original.
    reference = fit_locally(image_bytes, target_width, target_height, quality=95)
    size = _output_size(model, target_width, target_height)
    data = {
        "model": model,
        "prompt": _PROMPT,
        "size": size,
        "quality": "high" if model.startswith("gpt-image-2") else "medium",
        "output_format": "jpeg",
        "output_compression": "95",
        "n": "1",
    }
    # VERIFIED live 2026-08-11 against the real endpoint, not from docs. Only
    # full gpt-image-1 accepts this parameter. gpt-image-1-mini rejects it with
    # HTTP 400 "input_fidelity 'high' is not supported for gpt-image-1-mini",
    # and gpt-image-2 always uses high fidelity and does not accept the field.
    # The previous check excluded only gpt-image-2, which meant the then-current
    # gpt-image-1-mini default failed every single call.
    if _accepts_input_fidelity(model):
        data["input_fidelity"] = "high"
    try:
        response = post(
            _ENDPOINT,
            {"Authorization": f"Bearer {api_key}"},
            data,
            {"image[]": ("property.jpg", reference, "image/jpeg")},
            _TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise EnhancementError("the image service could not be reached") from exc
    edited = _response_image(response)
    try:
        width, height = image_dimensions(edited)
    except OSError as exc:
        raise EnhancementError("the image service returned an unreadable enlargement") from exc
    if assess(width, height, target_width, target_height).needs_model:
        raise EnhancementQualityError("the enlarged image was still too small")

    fitted = fit_locally(edited, target_width, target_height, quality=95)

    # The check that actually works. A model returning a *different house* is
    # the failure that matters on a listing flyer, and low-frequency composition
    # drift catches it: a faithful upscale of this photo measured 0.126 against
    # a 0.18 ceiling.
    if composition_distance(reference, fitted) > _MAX_COMPOSITION_DISTANCE:
        raise EnhancementQualityError("the enlargement changed the property photo too much")

    # Seam detection is deliberately NOT a gate here. Measured 2026-08-11, the
    # numeric detector cannot separate a pasted sky from a roofline or treeline
    # — a seamed image and the same image unseamed scored within 2% of each
    # other, and an earlier revision rejected 100% of enlargements including a
    # plain resize with no model in it. It is recorded for triage; the vision
    # pass over the rendered flyer is what decides whether the photo looks
    # wrong. The discarded seam detector could not distinguish a pasted sky
    # from an ordinary roofline or treeline; the rendered vision gate handles
    # visible seams with the rest of the flyer instead.
    return fitted
