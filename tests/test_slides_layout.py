"""A built flyer keeps every edge and every gap its design already had.

The three defects Chase reported on 2026-08-14 were all geometric and the
rendered vision pass reported none of them: a band running off the page edge, a
corner outside the page, and elements running into each other. Rectangles are
evidence where pixels were an opinion.
"""

from __future__ import annotations

from typing import Any

from gable.slides import layout

PT = layout.EMU_PER_POINT


def _shape(
    object_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str = "",
) -> dict[str, Any]:
    """One axis-aligned element, positioned in points."""
    element: dict[str, Any] = {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": width * PT},
            "height": {"magnitude": height * PT},
        },
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": x * PT, "translateY": y * PT},
    }
    if text:
        element["shape"] = {"text": {"textElements": [{"textRun": {"content": text}}]}}
    return element


def _deck(*elements: dict[str, Any]) -> dict[str, Any]:
    """A one-slide 810x1012pt presentation, the shape all six designs use."""
    return {
        "pageSize": {"width": {"magnitude": 810 * PT}, "height": {"magnitude": 1012 * PT}},
        "slides": [{"objectId": "page-1", "pageElements": list(elements)}],
    }


def test_a_designs_own_overhang_is_the_designers_decision() -> None:
    """Open House's footer rule starts 408 points off the left edge on purpose."""
    design = _deck(_shape("rule", -408.6, 983.7, 648, 236))

    assert layout.regressions(design, _deck(_shape("rule", -408.6, 983.7, 648, 236))) == []


def test_a_band_pushed_further_off_the_page_is_reported() -> None:
    design = _deck(_shape("band", 0, 960, 810, 60))
    built = _deck(_shape("band", 0, 960, 884.7, 60))

    found = layout.regressions(design, built)

    assert len(found) == 1
    assert "past the right edge of the page" in found[0]
    assert "75 points" in found[0]


def test_a_photo_gable_created_outside_the_page_is_reported_with_no_prior_claim() -> None:
    """A created object has no design decision behind it to inherit."""
    design = _deck(_shape("well", 0, 100, 810, 380))
    built = _deck(_shape("gableHero_abc", -20, 100, 810, 380))

    found = layout.regressions(design, built)

    assert found == ["the property photo runs about 20 points past the left edge of the page."]


def test_a_portrait_overlapping_the_band_behind_it_is_not_a_defect() -> None:
    """Every one of these designs stands the agent over its own card."""
    design = _deck(
        _shape("band", 0, 700, 810, 300, "REALTOR"),
        _shape("name", 100, 720, 200, 40, "Kelsey Mahon"),
    )

    assert layout.regressions(design, design) == []


def test_text_that_only_collides_once_it_is_filled_is_reported() -> None:
    """Annie Nowicki's name wrapped onto the title beneath it."""
    design = _deck(
        _shape("name", 100, 700, 200, 30, "Kelsey Mahon"),
        _shape("title", 100, 740, 200, 30, "REALTOR"),
    )
    built = _deck(
        _shape("name", 100, 700, 200, 60, "Annie Nowicki"),
        _shape("title", 100, 740, 200, 30, "REALTOR"),
    )

    found = layout.regressions(design, built)

    assert len(found) == 1
    assert "overlap" in found[0]
    assert "Annie Nowicki" in found[0]


def test_a_hairline_touch_between_two_boxes_is_not_a_defect() -> None:
    design = _deck(
        _shape("name", 100, 700, 200, 30, "Kelsey Mahon"),
        _shape("title", 100, 730, 200, 30, "REALTOR"),
    )
    built = _deck(
        _shape("name", 100, 700, 200, 31, "Annie Nowicki"),
        _shape("title", 100, 730, 200, 30, "REALTOR"),
    )

    assert layout.regressions(design, built) == []


def test_a_group_child_is_measured_where_it_renders() -> None:
    """A child's transform is relative to its group, which is the trap."""
    grouped = {
        "objectId": "group-1",
        "size": {"width": {"magnitude": 100 * PT}, "height": {"magnitude": 100 * PT}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 700 * PT, "translateY": 0},
        "elementGroup": {"children": [_shape("child", 60, 0, 100, 50)]},
    }

    found = layout.boxes(_deck(grouped))

    assert [box.object_id for box in found] == ["child"]
    assert found[0].x == 760 * PT
    assert found[0].right == 860 * PT


def test_an_unreadable_page_size_reports_nothing_rather_than_guessing() -> None:
    built = {"slides": [{"objectId": "page-1", "pageElements": []}]}

    assert layout.regressions(built, built) == []


def test_the_worst_defect_is_reported_first() -> None:
    design = _deck(_shape("a", 0, 0, 810, 100), _shape("b", 0, 200, 810, 100))
    built = _deck(_shape("a", 0, 0, 830, 100), _shape("b", 0, 200, 900, 100))

    found = layout.regressions(design, built)

    assert len(found) == 2
    assert "90 points" in found[0]
    assert "20 points" in found[1]


def test_a_photo_inherits_the_overhang_of_the_frame_it_replaced() -> None:
    """Sold's photo well starts three points off the left edge, by design."""
    design = _deck(_shape("well", -3.1, 0, 808.8, 480))
    built = _deck(_shape("gableHero_abc", -3.1, 0, 808.8, 480))

    assert layout.regressions(design, built) == []


def test_a_photo_pushed_beyond_the_frame_it_replaced_is_still_reported() -> None:
    design = _deck(_shape("well", -3.1, 0, 808.8, 480))
    built = _deck(_shape("gableHero_abc", -40, 0, 808.8, 480))

    found = layout.regressions(design, built)

    assert len(found) == 1
    assert "the property photo" in found[0]
