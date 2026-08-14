"""A rectangular portrait must not paint over what the cut-out left clear."""

from __future__ import annotations

from gable.slides.framing import MIN_KEPT_AREA, clear_of_neighbours

#: A frame 100 wide and 100 tall at the origin, in the same units throughout.
FRAME = (0.0, 0.0, 100.0, 100.0)


def test_a_band_below_the_frame_pulls_its_bottom_up() -> None:
    """New Listing's footer: the frame reached 16pt into it and covered the slogan."""
    footer = (-50.0, 90.0, 400.0, 40.0)

    assert clear_of_neighbours(FRAME, [footer]) == (0.0, 0.0, 100.0, 90.0)


def test_text_beside_the_frame_pulls_its_edge_in() -> None:
    """New Listing with Open House: the portrait covered the start of REALTOR."""
    title = (90.0, 40.0, 120.0, 20.0)

    assert clear_of_neighbours(FRAME, [title]) == (0.0, 0.0, 90.0, 100.0)


def test_a_background_the_frame_sits_inside_is_left_alone() -> None:
    """Clipping against the page's own backdrop would erase the frame."""
    backdrop = (-500.0, -500.0, 2000.0, 2000.0)

    assert clear_of_neighbours(FRAME, [backdrop]) == FRAME


def test_an_element_that_does_not_touch_the_frame_changes_nothing() -> None:
    """Only a real overlap is a reason to move a designer's frame."""
    elsewhere = (500.0, 500.0, 50.0, 50.0)

    assert clear_of_neighbours(FRAME, [elsewhere]) == FRAME


def test_a_clip_that_would_take_too_much_is_refused() -> None:
    """Guessing at a much smaller portrait is worse than the overlap."""
    across_the_middle = (-50.0, 20.0, 400.0, 400.0)

    assert clear_of_neighbours(FRAME, [across_the_middle]) == FRAME


def test_the_side_that_costs_least_is_the_one_that_moves() -> None:
    """A corner overlap has two answers; the larger remaining frame wins."""
    corner = (95.0, 5.0, 60.0, 200.0)

    kept = clear_of_neighbours(FRAME, [corner])

    assert kept == (0.0, 0.0, 95.0, 100.0)
    assert kept[2] * kept[3] >= FRAME[2] * FRAME[3] * MIN_KEPT_AREA


def test_several_neighbours_each_take_their_own_side() -> None:
    """Both of the live defects can appear on one design at once."""
    footer = (-50.0, 92.0, 400.0, 40.0)
    title = (94.0, 40.0, 120.0, 20.0)

    assert clear_of_neighbours(FRAME, [footer, title]) == (0.0, 0.0, 94.0, 92.0)


def test_a_zero_sized_neighbour_is_ignored() -> None:
    """A malformed element must not silently clip a real frame away."""
    assert clear_of_neighbours(FRAME, [(10.0, 10.0, 0.0, 0.0)]) == FRAME
