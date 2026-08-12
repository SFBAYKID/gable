"""What the template measurement must guarantee.

The fingerprint is the whole point: it decides whether a stored measurement is
still true. So the tests that matter are the ones about *stability* — that an
unchanged design hashes identically across reads, and that float noise in the
last bits does not invent a new version. A fingerprint that drifts is worse than
none, because it sends every template back for re-confirmation on every poll.
"""

from __future__ import annotations

from typing import Any

from gable.slides.measure import (
    EMU_PER_INCH,
    differences,
    geometry_fingerprint,
    measure,
    structural_fingerprint,
)


def _text_element(
    object_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    size_pt: float = 14.0,
) -> dict[str, Any]:
    """A shape holding one run of text, shaped like the Slides payload."""
    return {
        "objectId": object_id,
        "transform": {"scaleX": 1.0, "scaleY": 1.0, "translateX": x, "translateY": y},
        "size": {"width": {"magnitude": width}, "height": {"magnitude": height}},
        "shape": {
            "shapeType": "TEXT_BOX",
            "shapeProperties": {
                "contentAlignment": "TOP",
                "autofit": {"autofitType": "SHAPE_AUTOFIT", "fontScale": 1},
            },
            "text": {
                "textElements": [
                    {"paragraphMarker": {"style": {"alignment": "CENTER"}}},
                    {
                        "textRun": {
                            "content": text,
                            "style": {
                                "fontSize": {"magnitude": size_pt},
                                "weightedFontFamily": {"fontFamily": "Open Sans", "weight": 400},
                            },
                        }
                    },
                ]
            },
        },
    }


def _presentation(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """A one-slide presentation at the real 11.25 x 14.06in Instagram ratio."""
    return {
        "pageSize": {
            "width": {"magnitude": 11.25 * EMU_PER_INCH},
            "height": {"magnitude": 14.06 * EMU_PER_INCH},
        },
        "slides": [{"objectId": "p1", "pageElements": elements}],
    }


def test_measures_absolute_position_and_size() -> None:
    """Position and size come back in EMU, rounded to whole units."""
    payload = _presentation(
        [
            _text_element(
                "a",
                1.0 * EMU_PER_INCH,
                2.0 * EMU_PER_INCH,
                3.0 * EMU_PER_INCH,
                0.5 * EMU_PER_INCH,
                "Phone",
            )
        ]
    )
    found = measure(payload)
    element = found.by_id("a")
    assert element is not None
    assert element.x_emu == EMU_PER_INCH
    assert element.y_emu == 2 * EMU_PER_INCH
    assert element.width_emu == 3 * EMU_PER_INCH
    assert element.right_emu == 4 * EMU_PER_INCH
    assert element.text == "Phone"


def test_resolves_grouped_children_to_absolute_coordinates() -> None:
    """A grouped child's raw transform is relative; the measurement is not.

    This is the trap the whole module rests on: 44 of the 45 real designs wrap
    their artwork in an elementGroup, so a measurement that trusted the raw
    translate would place every element in the wrong spot.
    """
    child = _text_element(
        "child", 1.0 * EMU_PER_INCH, 1.0 * EMU_PER_INCH, EMU_PER_INCH, EMU_PER_INCH, "x"
    )
    group = {
        "objectId": "g",
        "transform": {
            "scaleX": 2.0,
            "scaleY": 2.0,
            "translateX": 3.0 * EMU_PER_INCH,
            "translateY": 0.0,
        },
        "elementGroup": {"children": [child]},
    }
    element = measure(_presentation([group])).by_id("child")
    assert element is not None
    # 3in offset + (1in child translate x 2 scale) = 5in, and the size scales too.
    assert element.x_emu == 5 * EMU_PER_INCH
    assert element.width_emu == 2 * EMU_PER_INCH
    assert element.group_path == ("g",)


def test_fingerprint_is_stable_across_identical_payloads() -> None:
    """The same design measured twice must hash identically."""
    payload = _presentation([_text_element("a", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone")])
    assert measure(payload).structural_fingerprint == measure(payload).structural_fingerprint


def test_fingerprint_absorbs_sub_emu_float_noise() -> None:
    """Slides returns floats that wobble in the last bits between reads.

    Without rounding, an unchanged template would produce a new fingerprint on
    every poll and every version would need re-confirming forever.
    """
    clean = _presentation([_text_element("a", 100000.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone")])
    noisy = _presentation(
        [_text_element("a", 100000.0000001, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone")]
    )
    assert measure(clean).structural_fingerprint == measure(noisy).structural_fingerprint


def test_fingerprint_changes_when_an_element_moves() -> None:
    """A real move must be caught, or a changed design renders as a stale one."""
    before = _presentation([_text_element("a", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone")])
    after = _presentation(
        [_text_element("a", 0.0, 0.25 * EMU_PER_INCH, EMU_PER_INCH, EMU_PER_INCH, "Phone")]
    )
    assert measure(before).structural_fingerprint != measure(after).structural_fingerprint
    assert measure(before).geometry_fingerprint != measure(after).geometry_fingerprint


def test_restyle_moves_structural_fingerprint_but_not_geometry() -> None:
    """A colour or font change is a new version even with nothing moved.

    This is why there are two hashes: the structural one gates confirmation, the
    geometry one lets a change report say *what kind* of change happened.
    """
    before = _presentation(
        [_text_element("a", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone", 14.0)]
    )
    after = _presentation([_text_element("a", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone", 18.0)])
    first, second = measure(before), measure(after)
    assert first.structural_fingerprint != second.structural_fingerprint
    assert first.geometry_fingerprint == second.geometry_fingerprint


def test_differences_separates_a_text_fill_from_a_font_mutation() -> None:
    """Filling a placeholder is authorised; shrinking the type is not.

    The 6:32 PM flyer had zero position drift and eleven font reductions Gable
    introduced. The report has to name those separately from the fills, or the
    real defect stays buried among expected changes.
    """
    template = _presentation(
        [_text_element("price", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "[PRICE]", 29.9)]
    )
    output = _presentation(
        [_text_element("price", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "$1,100,000", 17.4)]
    )
    lines = differences(measure(template), measure(output))
    assert any("font size" in line for line in lines)
    assert any("text" in line and "PRICE" in line for line in lines)
    assert not any("moved" in line for line in lines)


def test_differences_reports_added_and_removed_elements() -> None:
    """A placed photo is an added element, not a mutated one."""
    before = _presentation([_text_element("a", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone")])
    after = _presentation(
        [
            _text_element("a", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone"),
            _text_element("b", 0.0, EMU_PER_INCH, EMU_PER_INCH, EMU_PER_INCH, "Email"),
        ]
    )
    lines = differences(measure(before), measure(after))
    assert any(line.startswith("added") for line in lines)
    assert not any(line.startswith("removed") for line in lines)

    reverse = differences(measure(after), measure(before))
    assert any(line.startswith("removed") for line in reverse)


def test_identical_measurements_report_no_differences() -> None:
    """The common case: nothing changed, so nothing is reported."""
    payload = _presentation([_text_element("a", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone")])
    assert differences(measure(payload), measure(payload)) == []


def test_empty_presentation_measures_without_raising() -> None:
    """A payload with no pages is a failed onboarding, not a crash.

    It must still produce a measurement so the caller can record *why* the
    template could not be confirmed.
    """
    found = measure({})
    assert found.elements == ()
    assert found.hero is None
    assert found.structural_fingerprint
    assert structural_fingerprint(found) == found.structural_fingerprint
    assert geometry_fingerprint(found) == found.geometry_fingerprint


def test_captures_alignment_and_autofit() -> None:
    """Alignment and autofit are design, and a change to either is a new version.

    Autofit matters especially: every contact box in the real designs carries
    SHAPE_AUTOFIT, and whether that is set changes what happens when a real
    value is longer than its placeholder.
    """
    payload = _presentation([_text_element("a", 0.0, 0.0, EMU_PER_INCH, EMU_PER_INCH, "Phone")])
    element = measure(payload).by_id("a")
    assert element is not None
    assert element.paragraph_alignment == "CENTER"
    assert element.content_alignment == "TOP"
    assert element.autofit_type == "SHAPE_AUTOFIT"
    assert element.runs[0].font_family == "Open Sans"
