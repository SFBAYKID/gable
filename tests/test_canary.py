"""A design is built once with sample values before any listing is."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from gable.pipeline import canary
from gable.slides.layout import EMU_PER_POINT as PT

TEXT = ["[PROPERTY ADDRESS]", "[PRICE]", "AGENT NAME", "Phone"]


def _shape(object_id: str, x: float, y: float, w: float, h: float) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "size": {"width": {"magnitude": w * PT}, "height": {"magnitude": h * PT}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": x * PT, "translateY": y * PT},
    }


def _deck(*elements: dict[str, Any]) -> dict[str, Any]:
    return {
        "pageSize": {"width": {"magnitude": 810 * PT}, "height": {"magnitude": 1012 * PT}},
        "slides": [{"objectId": "p", "pageElements": list(elements)}],
    }


class Fakes:
    """Every seam recorded, with knobs for each way a build can go wrong."""

    def __init__(self) -> None:
        """Start clean: every placement succeeds and the layout matches."""
        self.trashed: list[str] = []
        self.filled: dict[str, str] = {}
        self.output_text: list[str] = []
        self.fill_result: int | None = None
        self.place_photo_ok = True
        self.place_headshot_ok: bool | None = True
        self.built = _deck(_shape("band", 0, 960, 810, 52))
        self.source = _deck(_shape("band", 0, 960, 810, 52))

    def seams(self) -> canary.Seams:
        """The seams bound to this fake's current knobs."""
        return canary.Seams(
            read_slide_text=lambda fid: self.output_text if fid == "out" else TEXT,
            read_presentation=lambda fid: self.built if fid == "out" else self.source,
            read_text_boxes=lambda _fid: [],
            copy_template=self._copy,
            fill=self._fill,
            apply=lambda _fid, _reqs: None,
            place_photo=lambda *_a: self.place_photo_ok,
            place_headshot=lambda *_a: self.place_headshot_ok,
            trash=self.trashed.append,
            hero_url="http://198.51.100.7/sample.jpg",
            face_url="http://198.51.100.7/face.png",
        )

    def _copy(self, _template: str, name: str) -> tuple[str, str]:
        assert name.startswith(canary.COPY_NAME_PREFIX)
        return "out", "https://docs.google.com/presentation/d/out/edit"

    def _fill(self, _fid: str, pairs: dict[str, str]) -> int:
        self.filled = pairs
        self.output_text = [pairs.get(text, text) for text in TEXT]
        return len(pairs) if self.fill_result is None else self.fill_result


def test_a_clean_design_builds_says_nothing_and_trashes_the_copy() -> None:
    fakes = Fakes()

    result = canary.dry_build("Sold", "tmpl", fakes.seams())

    assert result.clean
    assert canary.report("Sold", result) == ""
    assert fakes.trashed == ["out"]
    assert fakes.filled["[PRICE]"] == canary.SAMPLE_VALUES["price"]


def test_a_layout_regression_is_reported_and_the_copy_still_trashed() -> None:
    """Brittney Bushee's well, one edit earlier: found in the design thread, not hers."""
    fakes = Fakes()
    fakes.built = _deck(_shape("band", 0, 980, 810, 52))

    result = canary.dry_build("Under Contract", "tmpl", fakes.seams())

    assert not result.clean
    said = canary.report("Under Contract", result)
    assert "test flyer from the Under Contract design" in said
    assert "past the bottom edge" in said
    assert fakes.trashed == ["out"]


def test_an_unplaced_headshot_and_an_unfilled_field_are_both_named() -> None:
    fakes = Fakes()
    fakes.place_headshot_ok = False
    fakes.fill_result = 3
    original_fill = fakes._fill

    def partial(fid: str, pairs: dict[str, str]) -> int:
        original_fill(fid, pairs)
        fakes.output_text = [
            "[PRICE]" if text == "[PRICE]" else pairs.get(text, text) for text in TEXT
        ]
        return 3

    result = canary.dry_build("Sold", "tmpl", replace(fakes.seams(), fill=partial))

    said = canary.report("Sold", result)
    assert "price" in said and "did not go onto the flyer" in said
    assert "sample face is still on it" in said
    assert fakes.trashed == ["out"]


def test_a_build_that_raises_is_reported_not_raised() -> None:
    fakes = Fakes()

    def boom(_fid: str, _pairs: dict[str, str]) -> int:
        raise RuntimeError("Slides fell over")

    result = canary.dry_build("Sold", "tmpl", replace(fakes.seams(), fill=boom))

    assert "could not finish building a test flyer" in canary.report("Sold", result)
    assert fakes.trashed == ["out"], "the copy is trashed even when the build raised"


def test_a_testimonial_is_built_without_a_property_photo() -> None:
    fakes = Fakes()
    fakes.place_photo_ok = False  # would be a finding on a design with a photo well

    result = canary.dry_build("Client Review Post", "tmpl", fakes.seams())

    assert "property photo" not in canary.report("Client Review Post", result)
