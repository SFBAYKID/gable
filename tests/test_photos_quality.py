"""What the photo-size check must say, and when it must stay quiet.

Sizes below are the real ones from testing on 2026-08-12, against the real
`Just Sold — Thinking of Selling` frame at 1078x504.
"""

from __future__ import annotations

from gable.photos.fit import assess
from gable.photos.quality import Grade, judge, wanted_size

FRAME = (1078, 504)


def test_a_big_enough_photo_produces_no_message() -> None:
    """The common case has to be silent, or the warning becomes noise."""
    verdict = judge(assess(2200, 1030, *FRAME))
    assert verdict.grade is Grade.GOOD
    assert verdict.say == ""
    assert not verdict.should_warn


def test_an_exactly_sized_photo_is_good() -> None:
    """No enlargement at all is the happy path."""
    verdict = judge(assess(1078, 504, *FRAME))
    assert verdict.grade is Grade.GOOD


def test_the_smallest_test_photo_is_called_out_as_poor() -> None:
    """`images.jpg` at 266x189 was enlarged four times and looked blurred."""
    verdict = judge(assess(266, 189, *FRAME))
    assert verdict.grade is Grade.POOR
    assert "266x189" in verdict.say
    assert "blurred" in verdict.say


def test_a_mildly_small_photo_is_called_soft_not_poor() -> None:
    """738x414 needs about 1.5x. Usable, worth mentioning, not alarming."""
    verdict = judge(assess(738, 414, *FRAME))
    assert verdict.grade is Grade.SOFT
    assert "soft" in verdict.say
    assert "blurred" not in verdict.say


def test_a_warning_never_blocks_the_flyer() -> None:
    """Carmen needs to see it to judge it.

    Refusing to build would leave her with the warning and nothing to look at,
    which makes the decision harder rather than easier.
    """
    for width, height in ((266, 189), (100, 60), (738, 414)):
        assert not judge(assess(width, height, *FRAME)).blocks_delivery


def test_a_badly_shaped_photo_is_flagged_even_when_large() -> None:
    """A tall photo in a wide frame loses most of itself to the crop."""
    verdict = judge(assess(2000, 3000, *FRAME))
    assert verdict.should_warn
    assert "cropped" in verdict.say


def test_the_wanted_size_is_stated_in_plain_words() -> None:
    """Answers "what size should the photo be?" without anyone doing sums."""
    line = wanted_size(assess(266, 189, *FRAME))
    assert "1078x504" in line
    assert "landscape" in line


def test_thresholds_are_ratios_so_they_travel_between_designs() -> None:
    """Frames differ by design, so a fixed pixel minimum would be wrong.

    Measured across the deck, hero frames run 0.55 to 2.17 in aspect. The same
    source is fine for a small frame and poor for a large one, and the check has
    to say so.
    """
    small = judge(assess(800, 600, 700, 500))
    large = judge(assess(800, 600, 3000, 2200))
    assert small.grade is Grade.GOOD
    assert large.grade is Grade.POOR
