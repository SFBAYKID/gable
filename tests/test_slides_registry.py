"""What the template registry must decide.

The property that matters is that nobody has to remember to re-measure. Carmen
edits designs in place and re-exports from Canva; neither announces itself, so
the system has to notice on its own — and it has to stay quiet when she opens a
file without changing it, or it would ask to be re-certified for doing nothing.
"""

from __future__ import annotations

from typing import Any

from gable.slides.measure import EMU_PER_INCH, TemplateMeasurement, measure
from gable.slides.registry import Decision, Outcome, StoredVersion, decide, needs_fetch


def _presentation(x: float = 0.0, text: str = "Phone", pt: float = 14.0) -> dict[str, Any]:
    """A one-slide design with a single text box, at the real slide size."""
    return {
        "pageSize": {
            "width": {"magnitude": 11.25 * EMU_PER_INCH},
            "height": {"magnitude": 14.06 * EMU_PER_INCH},
        },
        "slides": [
            {
                "objectId": "p1",
                "pageElements": [
                    {
                        "objectId": "a",
                        "transform": {
                            "scaleX": 1.0,
                            "scaleY": 1.0,
                            "translateX": x,
                            "translateY": 0.0,
                        },
                        "size": {
                            "width": {"magnitude": EMU_PER_INCH},
                            "height": {"magnitude": EMU_PER_INCH},
                        },
                        "shape": {
                            "shapeType": "TEXT_BOX",
                            "shapeProperties": {},
                            "text": {
                                "textElements": [
                                    {
                                        "textRun": {
                                            "content": text,
                                            "style": {"fontSize": {"magnitude": pt}},
                                        }
                                    }
                                ]
                            },
                        },
                    }
                ],
            }
        ],
    }


def _stored(
    measurement: TemplateMeasurement, drive_version: str, confirmed: bool = True
) -> StoredVersion:
    return StoredVersion(
        template_id="t1",
        version_number=1,
        structural_fingerprint=measurement.structural_fingerprint,
        geometry_fingerprint=measurement.geometry_fingerprint,
        drive_version=drive_version,
        confirmed=confirmed,
    )


def test_an_unchanged_template_is_reused_without_being_fetched() -> None:
    """The common case must cost one cheap Drive call and no Slides read."""
    first = measure(_presentation())
    stored = _stored(first, "12")
    assert needs_fetch(stored, "12") is False
    decision = decide(stored, "12", measurement=None)
    assert decision.outcome is Outcome.UNCHANGED
    assert decision.usable is True
    assert decision.measurement is None


def test_a_template_never_seen_before_is_measured_and_held_back() -> None:
    """A new design is recorded but not trusted until a person looks at it.

    The measurement contains inferences — which shape is the photo well, which
    literal takes the price — and an unchecked inference is a guess written down.
    """
    fresh = measure(_presentation())
    assert needs_fetch(None, "1") is True
    decision = decide(None, "1", measurement=fresh)
    assert decision.outcome is Outcome.NEW
    assert decision.version_number == 1
    assert decision.usable is False
    assert "have not seen before" in decision.say


def test_opening_a_file_without_editing_it_does_not_cut_a_version() -> None:
    """Drive's version increments on a save, even when nothing changed.

    Without this branch, every time Carmen opened a template to look at it the
    system would create a version and ask to be re-certified for doing nothing.
    """
    first = measure(_presentation())
    stored = _stored(first, "12")
    again = measure(_presentation())  # identical design, new Drive version
    decision = decide(stored, "13", measurement=again)
    assert decision.outcome is Outcome.TOUCHED
    assert decision.version_number == 1
    assert decision.usable is True
    assert decision.say == ""


def test_a_real_edit_creates_an_unconfirmed_new_version() -> None:
    """A moved element is a new version, and it stops rendering until checked."""
    first = measure(_presentation(x=0.0))
    stored = _stored(first, "12")
    edited = measure(_presentation(x=EMU_PER_INCH))
    decision = decide(stored, "13", measurement=edited, previous=first)
    assert decision.outcome is Outcome.CHANGED
    assert decision.version_number == 2
    assert decision.usable is False
    assert "version 2" in decision.say


def test_a_change_report_names_what_moved() -> None:
    """A bare "something changed" is not actionable; the delta is."""
    first = measure(_presentation(pt=14.0))
    stored = _stored(first, "12")
    edited = measure(_presentation(pt=24.0))
    decision = decide(stored, "13", measurement=edited, previous=first)
    assert decision.changes
    assert any("font size" in line for line in decision.changes)


def test_an_unconfirmed_template_is_not_usable_even_when_unchanged() -> None:
    """Confirmation is a gate, not a one-time formality."""
    first = measure(_presentation())
    stored = _stored(first, "12", confirmed=False)
    decision = decide(stored, "12", measurement=None)
    assert decision.outcome is Outcome.UNCHANGED
    assert decision.usable is False
    assert "not been confirmed" in decision.say


def test_judging_a_changed_template_without_measuring_it_is_refused() -> None:
    """Reporting a template as unchanged without looking would be a lie.

    The caller skipping the fetch is a bug, and it has to fail loudly rather
    than quietly certify a design nobody read.
    """
    first = measure(_presentation())
    stored = _stored(first, "12")
    try:
        decide(stored, "99", measurement=None)
    except ValueError as exc:
        assert "must be measured" in str(exc)
    else:  # pragma: no cover - the assertion above is the point
        raise AssertionError("a changed template must not be judged unmeasured")


def test_a_decision_is_a_plain_value() -> None:
    """Nothing here touches Drive or Slides, so it stays unit-testable."""
    assert isinstance(decide(None, "1", measurement=measure(_presentation())), Decision)
