"""Coverage for measured type metrics and the fits that depend on them."""

from __future__ import annotations

from typing import Any

import pytest

from gable.slides import fitting, preflight, typemetrics
from gable.slides.elements import font_family, font_weight

#: The Open House design's agent-name box, measured from the live template.
NAME_BOX_PT = 129.07
SAMPLE_PT = 22.12
FITTED_PT = 18.78


def shape(runs: list[dict[str, Any]], object_id: str = "p1_i115") -> dict[str, Any]:
    """One Slides shape carrying the given text runs."""
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": 3000000, "unit": "EMU"},
            "height": {"magnitude": 3000000, "unit": "EMU"},
        },
        "transform": {"scaleX": 0.546398, "scaleY": 0.124139, "unit": "EMU"},
        "shape": {"text": {"textElements": runs}},
    }


def run(content: str, size: float, family: str, weight: int) -> dict[str, Any]:
    """One text run with an explicit face, the way an imported deck reports it."""
    return {
        "textRun": {
            "content": content,
            "style": {
                "fontSize": {"magnitude": size, "unit": "PT"},
                "bold": weight >= 600,
                "weightedFontFamily": {"fontFamily": family, "weight": weight},
            },
        }
    }


def test_the_designs_own_faces_are_measured() -> None:
    """Every family the six designs draw text in has real advance widths."""
    for family in ("Open Sans", "EB Garamond", "Raleway"):
        for weight in (400, 700):
            assert typemetrics.measured(family, weight)


def test_an_unmeasured_face_says_so_rather_than_guessing() -> None:
    """A face outside the table returns None, so the caller keeps its margin."""
    assert not typemetrics.measured("Comic Sans MS", 400)
    assert typemetrics.advance_factor("anything", "Comic Sans MS", 400) is None


def test_the_family_name_is_read_the_way_the_api_spells_it() -> None:
    """Case and spacing in the reported family never decide the lookup."""
    assert typemetrics.measured("open  sans", 700)
    assert typemetrics.advance_factor("Ab", "OPEN SANS", 700) == pytest.approx(
        typemetrics.advance_factor("Ab", "Open Sans", 700)
    )


def test_a_weight_between_the_two_measured_ones_rounds_to_bold() -> None:
    """Anything at or above 600 is measured bold; below it, regular."""
    assert typemetrics._key("Open Sans", 600).endswith("|700")
    assert typemetrics._key("Open Sans", 599).endswith("|400")


def test_an_unmeasured_character_is_overstated_not_dropped() -> None:
    """A glyph outside the table costs the face's widest advance."""
    table = typemetrics.CHAR_ADVANCE["open sans|400"]
    widest = max(table.values())
    factor = typemetrics.advance_factor("中", "Open Sans", 400)
    assert factor == pytest.approx(widest)


def test_the_designs_sample_name_fits_the_box_it_was_drawn_for() -> None:
    """Louis Smith at 22.12pt sums to just inside the 129.07pt name box.

    Measured on the rendered template: it draws on one line. The sum being
    within a fraction of a point of the box is the whole reason the fit can be
    trusted to a two percent margin.
    """
    width = fitting.estimate_width_pt("Louis Smith", SAMPLE_PT, 700, "Open Sans")
    assert width == pytest.approx(128.99, abs=0.05)
    assert width < NAME_BOX_PT


def test_a_longer_name_that_wrapped_on_a_real_flyer_is_now_shrunk() -> None:
    """The Annie Nowicki regression: 18.78pt wraps, so the fit must go lower.

    Slides drew this name on two lines inside the Open House contact block and
    the second line landed on top of the Realtor title beneath it. The old
    class-based estimate called it a fit by a tenth of a point.
    """
    fit = fitting.fit_for(
        "p1_i115",
        "Annie Nowicki",
        FITTED_PT,
        NAME_BOX_PT * fitting.EMU_PER_POINT,
        weight=700,
        family="Open Sans",
    )

    assert fit.overflows
    assert fit.fitted_pt < FITTED_PT
    # Whatever size it lands on has to actually sit inside the box.
    assert (
        fitting.estimate_width_pt("Annie Nowicki", fit.fitted_pt, 700, "Open Sans") <= NAME_BOX_PT
    )
    assert not fit.too_small_to_read
    assert fitting.requests_for([fit])


def test_the_old_estimate_would_have_passed_the_name_that_wrapped() -> None:
    """The class model's 14 percent shortfall is why this table exists."""
    guessed = fitting.estimate_width_pt("Annie Nowicki", FITTED_PT, 700)
    measured = fitting.estimate_width_pt("Annie Nowicki", FITTED_PT, 700, "Open Sans")

    # It called a wrap a fit, and by enough that the safety margin could not save it.
    assert guessed < NAME_BOX_PT * fitting.SAFETY
    assert measured > NAME_BOX_PT
    assert measured / guessed > 1.14


def test_an_unmeasured_face_keeps_the_wider_margin() -> None:
    """The tight margin is only ever applied to a face that was measured."""
    measured_fit = fitting.fit_for(
        "a", "Kelsey Mahon", 20.0, 200 * fitting.EMU_PER_POINT, family="Open Sans"
    )
    guessed_fit = fitting.fit_for("a", "Kelsey Mahon", 20.0, 200 * fitting.EMU_PER_POINT)

    assert measured_fit.box_width_pt == pytest.approx(200 * fitting.MEASURED_SAFETY)
    assert guessed_fit.box_width_pt == pytest.approx(200 * fitting.SAFETY)


def test_the_widest_line_decides_overflow_not_the_longest() -> None:
    """A short line of wide letters overflows a long line of narrow ones."""
    widest = typemetrics.advance_factor("WWW", "Open Sans", 400)
    assert widest is not None
    width = fitting.estimate_width_pt("illicit\nWWW", 20.0, 400, "Open Sans")
    assert width == pytest.approx(widest * 20.0)


def test_the_trailing_newline_run_never_decides_the_face() -> None:
    """Slides leaves that run at the imported default, which is not the text's.

    Every filled box in these designs ends with a newline run styled Arial 400.
    Reading it would call bold Open Sans regular Arial, and both the weight and
    the width would then be wrong.
    """
    element = shape(
        [
            run("Annie Nowicki", FITTED_PT, "Open Sans", 700),
            run("\n", FITTED_PT, "Arial", 400),
        ]
    )

    assert font_family(element) == "Open Sans"
    assert font_weight(element) == 700


def test_a_shape_with_no_declared_face_reports_none() -> None:
    """An inherited face is left unmeasured rather than assumed."""
    element = shape([{"textRun": {"content": "Annie Nowicki", "style": {}}}])

    assert font_family(element) == ""
    assert not typemetrics.measured(font_family(element), 700)


def test_measured_boxes_reach_the_fitter_through_preflight() -> None:
    """The face read off the slide is the face the fit is computed in."""
    presentation = {
        "slides": [
            {
                "objectId": "p1",
                "pageElements": [
                    shape(
                        [
                            run("Annie Nowicki", FITTED_PT, "Open Sans", 700),
                            run("\n", FITTED_PT, "Arial", 400),
                        ]
                    )
                ],
            }
        ]
    }

    boxes = preflight.text_boxes(presentation)

    assert [box.family for box in boxes] == ["Open Sans"]
    assert boxes[0].weight == 700
    fits = fitting.plan_fits(boxes, dynamic=["Annie Nowicki"], single_line=["Annie Nowicki"])
    assert fits[0].overflows
