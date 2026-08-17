"""Choosing between cropping a photo and keeping all of it.

`assess` measured `crop_loss` from the first commit and used it for nothing, so
a photograph whose shape fought the frame was cropped as hard as filling that
frame demanded. The numbers here are the measured ones from two real flyers on
2026-08-17, not invented: a 1320x1918 upload into the Under Contract hero,
809x420pt at 1.924:1, and a 1000x1080 into the Sold hero at 1.923:1.

Does not handle: the pixel work itself, which `test_photo_fit` covers.
"""

from __future__ import annotations

import pytest

from gable.photos.fit import (
    MAX_TOLERABLE_CROP_LOSS,
    FitAction,
    assess,
)


def test_a_portrait_into_a_wide_frame_is_contained_not_gutted() -> None:
    """The real Wycombe numbers: 1320x1918 into an 809x420pt frame at 1.924:1.

    `crop_loss` was measured from the start and used for nothing, so filling the
    frame threw away 64% of Carmen's photograph — the whole front garden and the
    bottom of the porch. Chase: "the image is so cropped in the flyer".
    """
    decision = assess(1320, 1918, 1618, 840)

    assert decision.crop_loss > MAX_TOLERABLE_CROP_LOSS
    assert decision.action is FitAction.CONTAIN_WHOLE
    assert decision.needs_contained_fit
    assert not decision.needs_small_source_fit, "her photo is not low quality, just tall"


def test_a_near_square_into_a_wide_frame_is_contained() -> None:
    """The real Monastery numbers: 1000x1080 into a 648x337pt frame."""
    decision = assess(1000, 1080, 1296, 674)

    assert decision.crop_loss == pytest.approx(0.518, abs=0.005)
    assert decision.action is FitAction.CONTAIN_WHOLE


def test_an_ordinary_landscape_photo_still_fills_the_frame() -> None:
    """The common case must not change: these crop, as they always have.

    A 3:2 loses 22% and a 4:3 loses 31% in these frames, and 30% and 38% in the
    widest 2.14:1 hero. All well under the line, all still cropped to fill.
    """
    for source, target in (
        ((1800, 1200), (1618, 840)),
        ((1600, 1200), (1618, 840)),
        ((1600, 1200), (2156, 1008)),
        ((3000, 2000), (2156, 1008)),
    ):
        decision = assess(*source, *target)
        assert decision.crop_loss <= MAX_TOLERABLE_CROP_LOSS, source
        assert decision.action is FitAction.CROP, source
        assert not decision.needs_contained_fit, source


def test_the_line_sits_clear_of_the_worst_ordinary_crop() -> None:
    """38% is the worst an ordinary landscape source loses; the line is above it."""
    assert MAX_TOLERABLE_CROP_LOSS > 0.38


def test_a_small_source_is_still_told_apart_from_a_wrong_shape() -> None:
    """Only a genuinely small original earns "send a higher-quality version"."""
    small = assess(600, 400, 1618, 840)
    assert small.needs_small_source_fit
    assert small.needs_contained_fit

    tall = assess(1320, 1918, 1618, 840)
    assert not tall.needs_small_source_fit
    assert tall.needs_contained_fit
