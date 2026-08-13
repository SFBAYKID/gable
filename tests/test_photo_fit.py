"""Tests for fitting a photo to the hero frame.

The bias is toward the cost decision: `assess` deciding a model is needed when
one is not costs money on every flyer, and deciding one is not needed when it is
puts a visibly soft photo in front of a client. Both directions are tested.

The 1080x1350 frame and the 267x148 source are the real numbers from the first
end-to-end run, not invented ones.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from gable.photos.fit import (
    MAX_TOLERABLE_UPSCALE,
    FitAction,
    assess,
    fit_locally,
    image_dimensions,
)

FRAME_W, FRAME_H = 1080, 1350


def _png(width: int, height: int, colour: tuple[int, int, int] = (200, 120, 60)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


# --- the cost decision ------------------------------------------------------


def test_the_real_download_jpg_needs_a_model() -> None:
    """267x148 is the actual test image Chase supplied, and it is far too small."""
    a = assess(267, 148, FRAME_W, FRAME_H)
    assert a.action is FitAction.NEEDS_UPSCALE
    assert a.needs_model is True
    assert a.upscale_factor > MAX_TOLERABLE_UPSCALE


def test_a_normal_phone_photo_is_free() -> None:
    """A 4032x3024 phone photo has pixels to spare; cropping is all it needs."""
    a = assess(4032, 3024, FRAME_W, FRAME_H)
    assert a.action is FitAction.CROP
    assert a.is_free is True
    assert a.needs_model is False


def test_exact_frame_is_used_untouched() -> None:
    a = assess(FRAME_W, FRAME_H, FRAME_W, FRAME_H)
    assert a.action is FitAction.USE_AS_IS
    assert a.is_free is True


def test_same_shape_but_larger_only_downscales() -> None:
    a = assess(FRAME_W * 3, FRAME_H * 3, FRAME_W, FRAME_H)
    assert a.action is FitAction.DOWNSCALE
    assert a.is_free is True


def test_the_upscale_threshold_is_the_boundary() -> None:
    """Exactly 2x is tolerated; past it a model is required."""
    at_limit = assess(FRAME_W // 2, FRAME_H // 2, FRAME_W, FRAME_H)
    assert at_limit.upscale_factor == pytest.approx(MAX_TOLERABLE_UPSCALE)
    assert at_limit.needs_model is False
    assert at_limit.action is FitAction.LOCAL_ENLARGE

    past_limit = assess(FRAME_W // 3, FRAME_H // 3, FRAME_W, FRAME_H)
    assert past_limit.needs_model is True


def test_upscale_is_driven_by_the_binding_axis() -> None:
    """A photo wide enough but far too short still needs a model."""
    a = assess(4000, 200, FRAME_W, FRAME_H)
    assert a.action is FitAction.NEEDS_UPSCALE
    assert a.upscale_factor == pytest.approx(FRAME_H / 200)


# --- crop accounting --------------------------------------------------------


def test_a_wide_photo_loses_its_sides() -> None:
    a = assess(2000, 1000, FRAME_W, FRAME_H)  # 2.0 vs 0.8
    assert a.action is FitAction.CROP
    assert a.crop_loss == pytest.approx(1 - (0.8 / 2.0))


def test_a_tall_photo_loses_top_and_bottom() -> None:
    a = assess(1000, 4000, FRAME_W, FRAME_H)  # 0.25 vs 0.8
    assert a.crop_loss == pytest.approx(1 - (0.25 / 0.8))


def test_matching_aspect_loses_nothing() -> None:
    assert assess(2160, 2700, FRAME_W, FRAME_H).crop_loss == pytest.approx(0.0)


def test_a_rounding_difference_is_not_treated_as_a_crop() -> None:
    """1079x1350 must not be sent down a crop path for a rounding error."""
    assert assess(1079, 1350, FRAME_W, FRAME_H).crop_loss == pytest.approx(0.0)


# --- the reason is written for a human --------------------------------------


def test_every_action_explains_itself() -> None:
    for w, h in ((267, 148), (4032, 3024), (FRAME_W, FRAME_H), (FRAME_W * 2, FRAME_H * 2)):
        a = assess(w, h, FRAME_W, FRAME_H)
        assert a.reason and a.reason[0].islower() and len(a.reason) > 20


def test_the_upscale_reason_names_the_real_numbers() -> None:
    a = assess(267, 148, FRAME_W, FRAME_H)
    assert "267x148" in a.reason
    assert "1080x1350" in a.reason


# --- bad input --------------------------------------------------------------


@pytest.mark.parametrize(
    ("sw", "sh", "tw", "th"),
    [(0, 100, 10, 10), (100, 0, 10, 10), (100, 100, 0, 10), (100, 100, 10, 0), (-5, 10, 10, 10)],
)
def test_non_positive_dimensions_are_refused(sw: int, sh: int, tw: int, th: int) -> None:
    """A zero dimension is a corrupt upload; guessing past it stretches a photo."""
    with pytest.raises(ValueError, match="must be positive"):
        assess(sw, sh, tw, th)


# --- the free path actually works -------------------------------------------


def test_fit_produces_exactly_the_frame_size() -> None:
    out = fit_locally(_png(4032, 3024), FRAME_W, FRAME_H)
    assert image_dimensions(out) == (FRAME_W, FRAME_H)


def test_fit_handles_a_tall_source() -> None:
    out = fit_locally(_png(1000, 4000), FRAME_W, FRAME_H)
    assert image_dimensions(out) == (FRAME_W, FRAME_H)


def test_fit_handles_a_wide_source() -> None:
    out = fit_locally(_png(4000, 1000), FRAME_W, FRAME_H)
    assert image_dimensions(out) == (FRAME_W, FRAME_H)


def test_fit_output_is_jpeg_slides_will_accept() -> None:
    out = fit_locally(_png(2000, 2000), FRAME_W, FRAME_H)
    assert out[:3] == b"\xff\xd8\xff"


def test_fit_flattens_transparency_rather_than_failing() -> None:
    """A PNG with alpha would otherwise refuse to save as JPEG."""
    buf = io.BytesIO()
    Image.new("RGBA", (2000, 2000), (10, 20, 30, 128)).save(buf, format="PNG")
    out = fit_locally(buf.getvalue(), FRAME_W, FRAME_H)
    assert image_dimensions(out) == (FRAME_W, FRAME_H)


def test_fit_crops_from_the_centre() -> None:
    """The house is centred on a listing photo; edge-cropping decapitates it."""
    im = Image.new("RGB", (3000, 1000), (0, 0, 0))
    for x in range(1400, 1600):
        for y in range(400, 600):
            im.putpixel((x, y), (255, 0, 0))  # a red marker dead centre
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    out = fit_locally(buf.getvalue(), 400, 500)
    with Image.open(io.BytesIO(out)) as result:
        pixel = result.convert("RGB").getpixel((200, 250))
    assert isinstance(pixel, tuple)
    r, g, b = pixel[0], pixel[1], pixel[2]
    assert r > 200 and g < 60 and b < 60


@pytest.mark.parametrize(("tw", "th"), [(0, 100), (100, 0), (-1, 100)])
def test_fit_refuses_a_non_positive_frame(tw: int, th: int) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        fit_locally(_png(100, 100), tw, th)


def test_unreadable_bytes_raise_rather_than_stretch() -> None:
    """An unreadable upload deserves a specific message, not a silent fallback."""
    with pytest.raises(OSError):
        fit_locally(b"this is not an image", FRAME_W, FRAME_H)


def test_a_compressed_image_cannot_expand_past_the_process_pixel_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gable.photos.fit.MAX_SOURCE_PIXELS", 100)

    with pytest.raises(ValueError, match="dimensions exceed"):
        fit_locally(_png(20, 20), FRAME_W, FRAME_H)


# --- EXIF orientation -------------------------------------------------------


def _portrait_stored_landscape() -> bytes:
    """A JPEG stored 4000x3000 with Orientation=6 — an ordinary phone portrait.

    What a human sees is 3000x4000. Reporting the stored dimensions makes
    `assess` choose the wrong crop axis.
    """
    im = Image.new("RGB", (4000, 3000), (120, 160, 200))
    exif = im.getexif()
    exif[274] = 6  # Orientation: rotate 90 CW
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_dimensions_are_reported_as_displayed_not_as_stored() -> None:
    assert image_dimensions(_portrait_stored_landscape()) == (3000, 4000)


def test_a_rotated_photo_is_cropped_on_the_right_axis() -> None:
    """Stored dims would say trim 40% off the sides; displayed says 6% off the top."""
    stored = assess(4000, 3000, FRAME_W, FRAME_H)
    displayed = assess(3000, 4000, FRAME_W, FRAME_H)
    assert stored.crop_loss > 0.35
    assert displayed.crop_loss < 0.10

    w, h = image_dimensions(_portrait_stored_landscape())
    assert assess(w, h, FRAME_W, FRAME_H).crop_loss == pytest.approx(displayed.crop_loss)


def test_fitting_a_rotated_photo_still_produces_the_frame() -> None:
    out = fit_locally(_portrait_stored_landscape(), FRAME_W, FRAME_H)
    assert image_dimensions(out) == (FRAME_W, FRAME_H)
