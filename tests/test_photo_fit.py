"""Tests for fitting a photo to the hero frame.

The bias is toward the quality decision: a very small source must keep its full
composition without stretching its foreground past 2x or inventing detail.

The 1080x1350 frame and the 267x148 source are the real numbers from the first
end-to-end run, not invented ones.
"""

from __future__ import annotations

import io
import threading
from typing import cast

import pytest
from PIL import Image

from gable.photos.fit import (
    MAX_OUTPUT_PIXELS,
    MAX_TARGET_EDGE_PX,
    MAX_TOLERABLE_UPSCALE,
    FitAction,
    _vertical_crop_offset,
    assess,
    fit_bounded_portrait_locally,
    fit_bounded_source_locally,
    fit_locally,
    fit_small_source,
    image_dimensions,
    normalise_for_fitting,
)

FRAME_W, FRAME_H = 1080, 1350


def _png(width: int, height: int, colour: tuple[int, int, int] = (200, 120, 60)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


# --- the cost decision ------------------------------------------------------


def test_the_real_download_jpg_needs_the_small_source_fit() -> None:
    """267x148 is the actual test image Chase supplied, and it is far too small."""
    a = assess(267, 148, FRAME_W, FRAME_H)
    assert a.action is FitAction.SMALL_SOURCE
    assert a.needs_small_source_fit is True
    assert a.upscale_factor > MAX_TOLERABLE_UPSCALE


def test_a_normal_phone_photo_is_free() -> None:
    """A 4032x3024 phone photo has pixels to spare; cropping is all it needs."""
    a = assess(4032, 3024, FRAME_W, FRAME_H)
    assert a.action is FitAction.CROP
    assert a.is_free is True
    assert a.needs_small_source_fit is False


def test_exact_frame_is_used_untouched() -> None:
    a = assess(FRAME_W, FRAME_H, FRAME_W, FRAME_H)
    assert a.action is FitAction.USE_AS_IS
    assert a.is_free is True


def test_same_shape_but_larger_only_downscales() -> None:
    a = assess(FRAME_W * 3, FRAME_H * 3, FRAME_W, FRAME_H)
    assert a.action is FitAction.DOWNSCALE
    assert a.is_free is True


def test_the_upscale_threshold_is_the_boundary() -> None:
    """Exactly 2x is tolerated; past it uses the small-source composition."""
    at_limit = assess(FRAME_W // 2, FRAME_H // 2, FRAME_W, FRAME_H)
    assert at_limit.upscale_factor == pytest.approx(MAX_TOLERABLE_UPSCALE)
    assert at_limit.needs_small_source_fit is False
    assert at_limit.action is FitAction.LOCAL_ENLARGE

    past_limit = assess(FRAME_W // 3, FRAME_H // 3, FRAME_W, FRAME_H)
    assert past_limit.needs_small_source_fit is True


def test_upscale_is_driven_by_the_binding_axis() -> None:
    """A photo wide enough but far too short still needs the small-source fit."""
    a = assess(4000, 200, FRAME_W, FRAME_H)
    assert a.action is FitAction.SMALL_SOURCE
    assert a.upscale_factor == pytest.approx(FRAME_H / 200)


# --- crop accounting --------------------------------------------------------


def test_a_wide_photo_loses_its_sides() -> None:
    a = assess(2000, 1000, FRAME_W, FRAME_H)  # 2.0 vs 0.8
    assert a.crop_loss == pytest.approx(1 - (0.8 / 2.0))
    # 60% is past MAX_TOLERABLE_CROP_LOSS, so the whole photograph is kept over
    # its own blurred copy rather than having well over half its width cut off.
    assert a.action is FitAction.CONTAIN_WHOLE


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


def test_target_edge_limit_is_checked_before_decode() -> None:
    with pytest.raises(ValueError, match="target edge exceeds"):
        fit_locally(b"not an image", MAX_TARGET_EDGE_PX + 1, 1)


def test_target_area_limit_is_checked_before_decode() -> None:
    with pytest.raises(ValueError, match="output limit"):
        fit_small_source(b"not an image", 5001, 5000)


def test_assessment_rejects_an_oversized_target() -> None:
    with pytest.raises(ValueError, match="output limit"):
        assess(1, 1, 5001, 5000)


def test_bounded_headshot_fit_rejects_an_oversized_target_before_decode() -> None:
    with pytest.raises(ValueError, match="output limit"):
        fit_bounded_source_locally(b"not an image", 5001, 5000)


def test_target_pixel_limit_boundary_is_accepted_by_assessment() -> None:
    target_width = MAX_TARGET_EDGE_PX
    target_height = MAX_OUTPUT_PIXELS // target_width
    result = assess(1, 1, target_width, target_height)
    assert result.target_width * result.target_height == MAX_OUTPUT_PIXELS


def test_normalisation_edge_limit_is_checked_before_decode() -> None:
    with pytest.raises(ValueError, match="max edge exceeds"):
        normalise_for_fitting(b"not an image", max_edge_px=MAX_TARGET_EDGE_PX + 1)


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


def test_mike_small_source_fit_is_exact_and_keeps_foreground_at_two_x() -> None:
    """The real 275x183 Mike upload becomes a truthful 1078x504 composition."""
    source = Image.new("RGB", (275, 183), (210, 120, 30))
    # A bright boundary proves the contained foreground keeps every source edge.
    for x in range(source.width):
        source.putpixel((x, 0), (255, 255, 255))
        source.putpixel((x, source.height - 1), (255, 255, 255))
    for y in range(source.height):
        source.putpixel((0, y), (255, 255, 255))
        source.putpixel((source.width - 1, y), (255, 255, 255))
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")

    result = fit_small_source(encoded.getvalue(), 1078, 504, quality=100)

    assert image_dimensions(result) == (1078, 504)
    with Image.open(io.BytesIO(result)) as opened:
        fitted = opened.convert("RGB")
        # 275x183 at 2x is 550x366, centered at x=264 and y=69.
        top_left = cast(tuple[int, int, int], fitted.getpixel((264, 69)))
        bottom_right = cast(tuple[int, int, int], fitted.getpixel((813, 434)))
        assert top_left[0] > 220
        assert bottom_right[0] > 220
        # Outside the contained source is the intentionally dark backdrop.
        outside = cast(tuple[int, int, int], fitted.getpixel((30, 252)))
        inside = cast(tuple[int, int, int], fitted.getpixel((539, 252)))
        assert sum(outside) < sum(inside)


def test_small_source_fit_uses_only_pixels_from_the_upload() -> None:
    """A flat source can yield only its colour and a darker version of it."""
    result = fit_small_source(_png(120, 80, (180, 100, 40)), 900, 300, quality=100)

    with Image.open(io.BytesIO(result)) as opened:
        fitted = opened.convert("RGB")
        foreground = cast(tuple[int, int, int], fitted.getpixel((450, 150)))
        background = cast(tuple[int, int, int], fitted.getpixel((20, 150)))
    assert foreground[0] > background[0]
    assert foreground[1] > background[1]
    assert foreground[2] > background[2]


def test_fit_composites_semitransparent_pixels_over_white() -> None:
    """Discarding alpha would turn a translucent dark pixel fully opaque."""
    buf = io.BytesIO()
    Image.new("RGBA", (200, 200), (10, 20, 30, 128)).save(buf, format="PNG")

    out = fit_locally(buf.getvalue(), 200, 200, quality=100)

    with Image.open(io.BytesIO(out)) as fitted:
        pixel = cast(tuple[int, int, int], fitted.convert("RGB").getpixel((100, 100)))
    assert pixel == pytest.approx((132, 137, 142), abs=3)


def test_runtime_normalisation_does_not_reveal_fully_transparent_rgb() -> None:
    """Hidden RGB in a transparent Slack PNG must not become flyer content."""
    buf = io.BytesIO()
    Image.new("RGBA", (275, 183), (255, 0, 0, 0)).save(buf, format="PNG")

    prepared = normalise_for_fitting(buf.getvalue(), quality=100)
    fitted = fit_small_source(prepared, 1078, 504, quality=100)

    with Image.open(io.BytesIO(fitted)) as opened:
        rgb = opened.convert("RGB")
        centre = cast(tuple[int, int, int], rgb.getpixel((539, 252)))
        backdrop = cast(tuple[int, int, int], rgb.getpixel((20, 252)))
    assert centre == pytest.approx((255, 255, 255), abs=3)
    assert backdrop == pytest.approx((140, 140, 140), abs=3)


def test_palette_png_transparency_uses_the_same_white_matte() -> None:
    """Indexed PNG transparency must not bypass the alpha-safe conversion."""
    source = Image.new("P", (20, 20), 0)
    source.putpalette([255, 0, 0, *([0, 0, 0] * 255)])
    buf = io.BytesIO()
    source.save(buf, format="PNG", transparency=0)

    prepared = normalise_for_fitting(buf.getvalue(), quality=100)

    with Image.open(io.BytesIO(prepared)) as opened:
        pixel = cast(tuple[int, int, int], opened.convert("RGB").getpixel((10, 10)))
    assert pixel == pytest.approx((255, 255, 255), abs=3)


def test_large_transparent_png_is_downsampled_without_revealing_hidden_rgb() -> None:
    """The memory-saving early thumbnail retains alpha semantics."""
    source = Image.new("RGBA", (800, 400), (255, 0, 0, 0))
    buf = io.BytesIO()
    source.save(buf, format="PNG")

    prepared = normalise_for_fitting(buf.getvalue(), max_edge_px=400, quality=100)

    assert image_dimensions(prepared) == (400, 200)
    with Image.open(io.BytesIO(prepared)) as opened:
        pixel = cast(tuple[int, int, int], opened.convert("RGB").getpixel((200, 100)))
    assert pixel == pytest.approx((255, 255, 255), abs=3)


def test_concurrent_normalisation_has_one_memory_heavy_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different listing threads cannot decode two large uploads at once."""
    started = threading.Barrier(3)
    both_attempting = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    attempts = 0
    active = 0
    maximum_active = 0

    def guarded_work(_data: bytes, _edge: int, _quality: int) -> bytes:
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
            entered.set()
        try:
            assert release.wait(timeout=2)
            return b"prepared"
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr("gable.photos.fit._normalise_for_fitting_unlocked", guarded_work)

    results: list[bytes] = []

    def worker() -> None:
        nonlocal attempts
        started.wait()
        with state_lock:
            attempts += 1
            if attempts == 2:
                both_attempting.set()
        results.append(normalise_for_fitting(b"source"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    started.wait()
    assert both_attempting.wait(timeout=1)
    assert entered.wait(timeout=1)
    # Both workers called the public boundary. The first holds the process-wide
    # lock inside guarded_work, so the second cannot enter the heavy section.
    with state_lock:
        assert active == 1
        assert maximum_active == 1
    release.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert results == [b"prepared", b"prepared"]
    assert maximum_active == 1


def test_normalisation_failure_releases_the_decode_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One corrupt upload cannot strand every later listing behind the guard."""
    calls = 0

    def fail_once(_data: bytes, _edge: int, _quality: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("unreadable")
        return b"prepared"

    monkeypatch.setattr("gable.photos.fit._normalise_for_fitting_unlocked", fail_once)

    with pytest.raises(OSError, match="unreadable"):
        normalise_for_fitting(b"first")
    assert normalise_for_fitting(b"second") == b"prepared"


def test_fifty_megapixel_headshot_is_downsampled_before_cover_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A compressed Drive headshot cannot become a 50 MP RGB fit input."""
    from gable.photos import fit as fit_module

    source = Image.new("RGB", (10_000, 5_000), (90, 130, 170))
    buf = io.BytesIO()
    source.save(buf, format="JPEG", quality=70)
    observed_edges: list[int] = []
    original_flatten = fit_module._flatten_visible_pixels

    def observe_flatten(image: Image.Image) -> Image.Image:
        observed_edges.append(max(image.size))
        return original_flatten(image)

    monkeypatch.setattr(fit_module, "_flatten_visible_pixels", observe_flatten)

    fitted = fit_bounded_source_locally(
        buf.getvalue(),
        162,
        162,
        max_source_edge_px=2400,
    )

    assert image_dimensions(fitted) == (162, 162)
    assert observed_edges
    assert max(observed_edges) <= 2400


def test_concurrent_headshots_share_the_image_processing_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two portrait replacements cannot enter their heavy decode concurrently."""
    started = threading.Barrier(3)
    entered = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def guarded_normalise(_data: bytes, _edge: int, _quality: int) -> bytes:
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
            entered.set()
        try:
            assert release.wait(timeout=2)
            return b"prepared"
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(
        "gable.photos.fit._normalise_for_fitting_unlocked",
        guarded_normalise,
    )
    monkeypatch.setattr(
        "gable.photos.fit._fit_locally_unlocked",
        lambda _data, _width, _height, _quality: b"fitted",
    )
    results: list[bytes] = []

    def worker() -> None:
        started.wait()
        results.append(fit_bounded_source_locally(b"source", 162, 162))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    started.wait()
    assert entered.wait(timeout=1)
    with state_lock:
        assert active == 1
        assert maximum_active == 1
    release.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert results == [b"fitted", b"fitted"]
    assert maximum_active == 1


def test_failed_headshot_decode_releases_the_image_processing_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_once(_data: bytes, _edge: int, _quality: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("unreadable")
        return b"prepared"

    monkeypatch.setattr("gable.photos.fit._normalise_for_fitting_unlocked", fail_once)
    monkeypatch.setattr(
        "gable.photos.fit._fit_locally_unlocked",
        lambda _data, _width, _height, _quality: b"fitted",
    )

    with pytest.raises(OSError, match="unreadable"):
        fit_bounded_source_locally(b"first", 162, 162)
    assert fit_bounded_source_locally(b"second", 162, 162) == b"fitted"


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


def test_dimensions_do_not_decode_the_pixel_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _portrait_stored_landscape()

    def reject_decode(_image: Image.Image) -> None:
        raise AssertionError("pixel frame was decoded")

    monkeypatch.setattr(Image.Image, "load", reject_decode)
    assert image_dimensions(source) == (3000, 4000)


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


def _cutout_png(width: int = 600, height: int = 800) -> bytes:
    """A portrait cut-out: opaque subject, fully transparent surround."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(width // 4, 3 * width // 4):
        for y in range(height // 4, 3 * height // 4):
            image.putpixel((x, y), (30, 60, 90, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_a_cut_out_portrait_keeps_its_transparent_surround() -> None:
    """The Corner House agent is a cut-out over the address panel.

    Matting that alpha onto white turns it into an opaque rectangle whose
    corner covers the address box — seen live on the 2026-08-13 Louis Smith
    flyer, reported by two independent visual inspections.
    """
    fitted = fit_bounded_portrait_locally(_cutout_png(), 300, 400)

    with Image.open(io.BytesIO(fitted)) as out:
        assert out.format == "PNG"
        assert out.size == (300, 400)
        assert out.mode == "RGBA"
        surround = cast(tuple[int, int, int, int], out.getpixel((2, 2)))
        subject = cast(tuple[int, int, int, int], out.getpixel((150, 200)))
        assert surround[3] == 0, "the surround must stay transparent"
        assert subject[3] == 255, "the subject must stay opaque"


def test_the_property_path_still_mattes_alpha_onto_white() -> None:
    """The hero photo is a full-bleed JPEG; this contract must not change."""
    fitted = fit_bounded_source_locally(_cutout_png(), 300, 400)

    with Image.open(io.BytesIO(fitted)) as out:
        assert out.format == "JPEG"
        assert out.mode == "RGB"
        assert out.getpixel((2, 2)) == pytest.approx((255, 255, 255), abs=4)


def test_a_portrait_without_alpha_is_fitted_unharmed() -> None:
    """An ordinary opaque JPEG headshot must survive the PNG path."""
    source = io.BytesIO()
    Image.new("RGB", (900, 900), (200, 120, 60)).save(source, format="JPEG")

    fitted = fit_bounded_portrait_locally(source.getvalue(), 200, 400)

    with Image.open(io.BytesIO(fitted)) as out:
        assert out.format == "PNG"
        assert out.size == (200, 400)
        assert cast(tuple[int, int, int, int], out.getpixel((100, 200)))[3] == 255


def test_an_oversized_portrait_is_bounded_before_it_is_fitted() -> None:
    """The 1 GB droplet must not hold a full-size copy of a huge portrait."""
    with pytest.raises(ValueError):
        fit_bounded_portrait_locally(_cutout_png(), 0, 400)
    with pytest.raises(ValueError):
        fit_bounded_portrait_locally(_cutout_png(), 300, 400, max_source_edge_px=0)


def test_a_wide_frame_crop_takes_its_loss_from_the_bottom_not_the_roof() -> None:
    """The 2026-08-13 Dawn Rea failure: centring sliced both gable peaks.

    A 678x452 photo into the Sold design's 1078x504 hero keeps 317 rows. The
    old centre crop removed 67 from the top, cutting a roof peak that sat about
    60px down, while leaving a large empty lawn at the bottom.
    """
    source_height, kept_height = 452, 317
    excess = source_height - kept_height

    offset = _vertical_crop_offset(source_height, kept_height)

    assert offset < excess // 2, "the top must lose less than the bottom"
    assert offset < 60, "a roof peak 60px down must survive the crop"
    assert offset > 0, "pinning the roof to the frame edge still reads as cropped"


def test_a_portrait_upload_into_a_letterbox_keeps_its_roof() -> None:
    """Two flyers on 2026-08-15 were refused for cutting a dormer and a roof peak.

    A 1080x1149 upload into the Sold hero keeps about 505 rows and discards 644.
    Twenty percent of the discarded height is 129 rows of sky and roof; headroom
    belongs to the finished picture, not to how much was thrown away.
    """
    offset = _vertical_crop_offset(1149, 505)

    assert offset <= int(505 * 0.08) + 1
    assert offset > 0, "pinning the roof to the frame edge still reads as cropped"


def test_the_cap_barely_moves_the_confirmed_flyer() -> None:
    """The Dawn Rea crop is the standard, so the cap must not redefine it."""
    assert abs(_vertical_crop_offset(452, 317) - int((452 - 317) * 0.2)) <= 3


def test_the_vertical_crop_never_starts_above_the_image() -> None:
    """A source shorter than the kept height must not produce a negative box."""
    assert _vertical_crop_offset(300, 400) == 0
    assert _vertical_crop_offset(400, 400) == 0


def test_a_letterboxed_house_keeps_its_roofline_end_to_end() -> None:
    """Drive the real fit: a marked roof band must survive into the output."""
    width, height = 678, 452
    source = Image.new("RGB", (width, height), (120, 170, 220))
    # A distinct "roof" band where this photo's gable peaks actually sit.
    for y in range(58, 78):
        for x in range(width):
            source.putpixel((x, y), (10, 10, 10))
    raw = io.BytesIO()
    source.save(raw, format="JPEG", quality=95)

    fitted = fit_locally(raw.getvalue(), 1078, 504)

    with Image.open(io.BytesIO(fitted)) as out:
        assert out.size == (1078, 504)
        column = [cast(tuple[int, int, int], out.getpixel((539, y))) for y in range(out.height)]
        assert any(sum(pixel) < 120 for pixel in column), "the roof band was cropped away"


def test_side_trimming_stays_centred() -> None:
    """Horizontal framing is the photographer's; only height is re-anchored."""
    source = Image.new("RGB", (2000, 400), (30, 30, 30))
    for y in range(400):
        source.putpixel((1000, y), (250, 250, 250))
    raw = io.BytesIO()
    source.save(raw, format="JPEG", quality=95)

    fitted = fit_locally(raw.getvalue(), 500, 400)

    with Image.open(io.BytesIO(fitted)) as out:
        middle = cast(tuple[int, int, int], out.getpixel((250, 200)))
        assert sum(middle) > 600, "the centre column must remain centred"


def _alpha(image: Image.Image, x: int, y: int) -> int:
    """One pixel's alpha, as an int Mypy can reason about."""
    return cast("tuple[int, int, int, int]", image.getpixel((x, y)))[3]


def test_a_tall_cut_out_keeps_its_whole_head_in_a_square_slot() -> None:
    """New Listing with Open House draws a square well; the cut-outs are 2:3.

    Chase, 2026-08-14: "The top of his head is cut off in the image and that is
    not acceptable." A centre cover-crop took 150 pixels off the top of a
    600x900 portrait to make it square.
    """
    source = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    # A marker at the very top of the person, which a cover crop would remove.
    for x in range(280, 320):
        for y in range(0, 20):
            source.putpixel((x, y), (255, 0, 0, 255))
    raw = io.BytesIO()
    source.save(raw, format="PNG")

    fitted = Image.open(io.BytesIO(fit_bounded_portrait_locally(raw.getvalue(), 233, 233)))

    assert fitted.size == (233, 233)
    opaque = [(x, y) for x in range(233) for y in range(233) if _alpha(fitted, x, y) > 0]
    assert opaque, "the portrait vanished"
    # The crown survived: the topmost opaque pixel is the marker, not a cut edge.
    assert min(y for _x, y in opaque) < 233 * 0.30


def test_a_portrait_is_placed_on_the_slots_baseline() -> None:
    """These designs stand the agent on the card's baseline."""
    source = Image.new("RGBA", (600, 900), (10, 20, 30, 255))
    raw = io.BytesIO()
    source.save(raw, format="PNG")

    fitted = Image.open(io.BytesIO(fit_bounded_portrait_locally(raw.getvalue(), 300, 300)))

    assert fitted.size == (300, 300)
    assert _alpha(fitted, 150, 299) == 255, "the portrait does not reach the bottom"
    # A 2:3 cut-out in a square well fills the height, so the spare room is at
    # the sides — and it is transparent, which is why nothing looks letterboxed.
    assert _alpha(fitted, 2, 150) == 0, "the spare room should be transparent"


def test_a_portrait_matching_its_slot_is_unchanged_in_shape() -> None:
    """Under Contract's 2:3 well already matches the filed cut-outs exactly."""
    source = Image.new("RGBA", (600, 900), (10, 20, 30, 255))
    raw = io.BytesIO()
    source.save(raw, format="PNG")

    fitted = Image.open(io.BytesIO(fit_bounded_portrait_locally(raw.getvalue(), 200, 300)))

    assert fitted.size == (200, 300)
    assert _alpha(fitted, 100, 0) == 255
    assert _alpha(fitted, 100, 299) == 255
