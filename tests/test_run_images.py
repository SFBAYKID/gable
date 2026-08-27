"""Tests for the stage that puts the two photographs onto a built flyer.

The property photo and the headshot fail differently, and one of the designs
carries no property photo at all. Every case here is a way a flyer can be wrong
in front of a client: a missing house, a stranger's face, or -- the one that
started this file -- a photograph of a building over the agent's own portrait.
"""

from __future__ import annotations

from collections.abc import Callable

from gable.pipeline import run_images, run_reporting


def _never_called(*_args: object) -> bool:
    """A placement seam that must not run."""
    raise AssertionError("placement was attempted for a design that has no such well")


def _record_label(seen: list[str]) -> Callable[[str, str, dict[str, str], str], bool | None]:
    """A headshot placer that remembers the design name it was handed."""

    def placer(_file_id: str, _url: str, _values: dict[str, str], label: str) -> bool:
        seen.append(label)
        return True

    return placer


def test_a_design_with_no_property_photo_never_attempts_hero_placement() -> None:
    """Client Review Post is a quote and a face. There is no house to place."""
    attempts: list[str] = []

    unplaced = run_images.place_all(
        "run-1",
        "out-1",
        "Client Review Post",
        "",
        {"headshot": "http://example.invalid/porsher.jpg"},
        carries_a_photo=False,
        place_photo=_never_called,
        place_headshot=_record_label(attempts),
    )

    assert unplaced == ""
    # The label reaches the headshot placer, which is what lets it stop
    # excluding this design's one portrait well as though it were the hero.
    assert attempts == ["Client Review Post"]


def test_a_heroless_design_is_not_reported_as_missing_its_photo() -> None:
    """A False from a placement that never ran would fail every testimonial."""
    unplaced = run_images.place_all(
        "run-2",
        "out-1",
        "Client Review Post",
        "",
        {},
        carries_a_photo=False,
        place_photo=_never_called,
        place_headshot=lambda _f, _u, _v, _l: None,
    )

    assert unplaced == ""


def test_a_design_that_wants_a_photo_and_does_not_get_one_is_unfinished() -> None:
    """The photo is the point of a listing flyer, so this is never delivered."""
    unplaced = run_images.place_all(
        "run-3",
        "out-1",
        "Sold",
        "http://example.invalid/house.jpg",
        {"headshot": "http://example.invalid/lolo.jpg"},
        carries_a_photo=True,
        place_photo=lambda *_a: False,
        place_headshot=_never_called,
    )

    assert unplaced == run_images.NO_PHOTO
    assert "not sent it as finished" in run_images.unfinished(unplaced)


def test_a_known_headshot_well_that_keeps_the_sample_face_is_unfinished() -> None:
    """One agent's name beside another agent's photograph is the worst case."""
    unplaced = run_images.place_all(
        "run-4",
        "out-1",
        "Sold",
        "http://example.invalid/house.jpg",
        {"headshot": "http://example.invalid/lolo.jpg"},
        carries_a_photo=True,
        place_photo=lambda *_a: True,
        place_headshot=lambda *_a: False,
    )

    assert unplaced == run_images.NO_HEADSHOT


def test_a_design_with_no_headshot_well_is_still_deliverable() -> None:
    """None means the design has no slot, which is not a fault."""
    unplaced = run_images.place_all(
        "run-5",
        "out-1",
        "Sold",
        "http://example.invalid/house.jpg",
        {"headshot": "http://example.invalid/lolo.jpg"},
        carries_a_photo=True,
        place_photo=lambda *_a: True,
        place_headshot=lambda *_a: None,
    )

    assert unplaced == ""


def test_no_headshot_on_file_does_not_reach_the_placer() -> None:
    """An agent with no filed portrait is preflight's business, not this one."""
    unplaced = run_images.place_all(
        "run-6",
        "out-1",
        "Sold",
        "http://example.invalid/house.jpg",
        {},
        carries_a_photo=True,
        place_photo=lambda *_a: True,
        place_headshot=_never_called,
    )

    assert unplaced == ""


def test_a_testimonial_is_not_offered_a_reframe_it_cannot_have() -> None:
    """Delivered flyers used to end by asking for a photo that goes nowhere.

    On Porsher Howard's Client Review Post this arrived directly after five
    messages asking for a property photograph the design cannot hold.
    """
    assert run_reporting.reframe_offer(False) == ""
    assert "framed differently" in run_reporting.reframe_offer(True)
