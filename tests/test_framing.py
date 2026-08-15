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


# --- text wraps on word boundaries, the way Slides does --------------------


def test_a_long_address_is_sized_to_the_lines_it_really_needs() -> None:
    """Donald Clark's ZIP landed on a third line, on top of the panel below.

    The ribbon estimate said it fitted: the total advance width is under two
    249-point lines. Slides breaks at spaces, and "4812 Reisterstown Road," on
    its own is wider than the box.
    """
    from gable.slides import fitting

    address = "4812 Reisterstown Road, Baltimore, MD 21215"
    usable = 249 * fitting.MEASURED_SAFETY

    assert fitting.wrapped_line_count(address, 23.76, usable, 400, "Open Sans") == 3

    fit = fitting.fit_for("addr", address, 23.76, 249 * fitting.EMU_PER_POINT, 2, 400, "Open Sans")

    assert fit.overflows
    assert not fit.too_small_to_read
    assert fitting.wrapped_line_count(address, fit.fitted_pt, usable, 400, "Open Sans") == 2


def test_text_that_already_wraps_inside_its_box_is_left_alone() -> None:
    from gable.slides import fitting

    fit = fitting.fit_for(
        "addr",
        "32 S Prospect Ave, Catonsville, MD 21228",
        12.0,
        400 * fitting.EMU_PER_POINT,
        2,
        400,
        "Open Sans",
    )

    assert not fit.overflows
    assert fit.fitted_pt == 12.0


def test_a_single_word_wider_than_its_box_counts_the_lines_it_breaks_into() -> None:
    """Slides breaks inside a word rather than letting it overflow."""
    from gable.slides import fitting

    assert fitting.wrapped_line_count("A" * 60, 20.0, 60.0, 400, "Open Sans") > 1


def test_an_explicit_line_break_is_counted_as_its_own_line() -> None:
    from gable.slides import fitting

    assert fitting.wrapped_line_count("Rd\nCity", 10.0, 400.0, 400, "Open Sans") == 2


def test_a_two_line_count_is_fitted_across_the_lines_the_design_drew() -> None:
    r"""It is measured across two lines, not squeezed onto one.

    New Listing writes its counts as "4\nBedrooms". Forcing that onto one line
    shrank the label until it was unreadable.
    """
    from gable.slides import fitting

    box = fitting.TextBox(
        object_id="beds",
        text="3\nBedrooms",
        font_size_pt=20.0,
        width_emu=100 * fitting.EMU_PER_POINT,
        lines=2,
        weight=400,
        family="EB Garamond",
    )

    planned = fitting.plan_fits([box], dynamic={"3\nBedrooms"}, single_line={"3\nBedrooms"})

    assert len(planned) == 1
    assert not planned[0].too_small_to_read
