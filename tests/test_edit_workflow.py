"""Copy-readback-vision verification for post-delivery flyer edits."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from gable.pipeline.edit_workflow import EditWorkflow
from gable.pipeline.vision import Inspection, InspectionProblemKind, InspectionRemedy


def _text(
    object_id: str, value: str, *, points: float = 20, width: float = 4_000_000
) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "shape": {
            "text": {
                "textElements": [
                    {
                        "textRun": {
                            "content": value,
                            "style": {
                                "fontSize": {"magnitude": points, "unit": "PT"},
                                "weightedFontFamily": {"weight": 400},
                            },
                        }
                    }
                ]
            }
        },
        "size": {
            "width": {"magnitude": width, "unit": "EMU"},
            "height": {"magnitude": 600_000, "unit": "EMU"},
        },
        "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"},
    }


def _image(object_id: str) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "image": {"contentUrl": "https://example.test/signed"},
        "size": {
            "width": {"magnitude": 4_000_000, "unit": "EMU"},
            "height": {"magnitude": 2_000_000, "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": 100,
            "translateY": 200,
            "unit": "EMU",
        },
    }


def _presentation(*, address_width: float = 4_000_000) -> dict[str, Any]:
    return {
        "presentationId": "source",
        "pageSize": {
            "width": {"magnitude": 10_000_000, "unit": "EMU"},
            "height": {"magnitude": 12_500_000, "unit": "EMU"},
        },
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    _text(
                        "address",
                        "703 Perception Way, Aberdeen, MD 21001",
                        points=24,
                        width=address_width,
                    ),
                    _text("price", "$615,000", points=30),
                    _text("phone", "410.456.3564", points=16),
                    _image("gableHero_source"),
                    _image("gableFace_source"),
                ],
            }
        ],
    }


class Harness:
    """Keep a source and a separate mutable draft for one workflow run."""

    def __init__(self, source: dict[str, Any] | None = None) -> None:
        """Start with one canonical flyer and no draft copy."""
        self.source = source or _presentation()
        self.draft: dict[str, Any] | None = None
        self.applied: list[list[dict[str, Any]]] = []
        self.inspection = Inspection(looks_right=True, confident=True)
        self.rendered: list[str] = []
        self.change_extra_leaf = False

    def copy(self, source_id: str, edit_id: str) -> tuple[str, str]:
        """Create one in-memory version without changing the source."""
        assert source_id == "source"
        assert edit_id == "edit-1"
        self.draft = deepcopy(self.source)
        self.draft["presentationId"] = "draft"
        return "draft", "https://docs.example/draft"

    def read(self, file_id: str) -> dict[str, Any]:
        """Read the canonical file or current draft snapshot."""
        if file_id == "source":
            return deepcopy(self.source)
        assert file_id == "draft" and self.draft is not None
        return deepcopy(self.draft)

    def apply(self, file_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply the narrow request shapes emitted by the production tools."""
        assert file_id == "draft" and self.draft is not None
        self.applied.append(requests)
        replies: list[dict[str, Any]] = []
        leaves = {
            element["objectId"]: element for element in self.draft["slides"][0]["pageElements"]
        }
        for request in requests:
            if update := request.get("updateTextStyle"):
                element = leaves[update["objectId"]]
                style = element["shape"]["text"]["textElements"][0]["textRun"]["style"]
                style.update(deepcopy(update["style"]))
                replies.append({})
            elif replace := request.get("replaceAllText"):
                find = replace["containsText"]["text"]
                replacement = replace["replaceText"]
                changed = 0
                for element in leaves.values():
                    text_run = (
                        element.get("shape", {})
                        .get("text", {})
                        .get("textElements", [{}])[0]
                        .get("textRun")
                    )
                    if text_run and find in text_run.get("content", ""):
                        text_run["content"] = text_run["content"].replace(find, replacement)
                        changed += 1
                replies.append({"replaceAllText": {"occurrencesChanged": changed}})
            elif update := request.get("updatePageElementTransform"):
                element = leaves[update["objectId"]]
                transform = update["transform"]
                if update["applyMode"] == "ABSOLUTE":
                    element["transform"] = deepcopy(transform)
                else:
                    element["transform"]["translateX"] = (
                        element["transform"].get("translateX", 0) + transform["translateX"]
                    )
                    element["transform"]["translateY"] = (
                        element["transform"].get("translateY", 0) + transform["translateY"]
                    )
                replies.append({})
            else:
                update = request.get("updateLineProperties") or request.get("updateShapeProperties")
                assert update is not None
                leaves[update["objectId"]]["verifiedStyleChange"] = deepcopy(update)
                replies.append({})
        if self.change_extra_leaf:
            leaves["phone"]["transform"]["translateX"] = 999
        return {"replies": replies}

    def thumbnail(self, file_id: str) -> bytes:
        """Record rendering only after deterministic readback passes."""
        self.rendered.append(file_id)
        return b"rendered flyer"

    def look(self, image: bytes) -> Inspection:
        """Return the configured strict visual result."""
        assert image == b"rendered flyer"
        return self.inspection

    def workflow(self) -> EditWorkflow:
        """Bind this harness to the production workflow seams."""
        return EditWorkflow(self.copy, self.read, self.apply, self.thumbnail, self.look)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("set_font_size", {"target": "price", "points": 24}),
        ("set_colour", {"target": "price", "colour": "navy"}),
        (
            "correct_field",
            {"current": "$615,000", "replacement": "$620,000"},
        ),
        ("resize_photo", {"which": "hero", "factor": 1.05}),
        ("resize_photo", {"which": "headshot", "factor": 0.95}),
        (
            "move_element",
            {"target": "hero photo", "dx_points": 2, "dy_points": -1},
        ),
    ],
)
def test_every_supported_edit_changes_only_a_copy_and_requires_vision(
    tool: str,
    arguments: dict[str, Any],
) -> None:
    harness = Harness()
    original = deepcopy(harness.source)

    result = harness.workflow().execute(
        "source",
        "edit-1",
        tool,
        arguments,
        field_values={
            "price": ("$615,000",),
            "address": ("703 Perception Way, Aberdeen, MD 21001",),
        },
        required_values=(
            "703 Perception Way, Aberdeen, MD 21001",
            "$615,000",
            "410.456.3564",
        ),
    )

    assert result.verified
    assert "Open the updated flyer" in result.message
    assert result.draft_file_id == "draft"
    assert harness.source == original
    assert harness.draft != harness.source
    assert harness.rendered == ["draft"]


def test_a_literal_correction_updates_the_required_readback_value() -> None:
    harness = Harness()

    result = harness.workflow().execute(
        "source",
        "edit-1",
        "correct_field",
        {"current": "$615,000", "replacement": "$620,000"},
        field_values={"price": ("$615,000",)},
        required_values=("$615,000", "410.456.3564"),
    )

    assert result.verified
    assert result.plan is not None and result.plan.target == "price"


@pytest.mark.parametrize(
    ("inspection", "phrase"),
    [
        (Inspection(False, False, checked=False), "could not run"),
        (Inspection(False, False), "inconclusive"),
        (
            Inspection(
                False,
                True,
                problems=["The address overlaps the price."],
                remedy=InspectionRemedy.REVIEW,
                problem_kinds=(InspectionProblemKind.LAYOUT,),
            ),
            "address overlaps",
        ),
    ],
)
def test_every_nonpositive_visual_result_hides_the_draft_link(
    inspection: Inspection,
    phrase: str,
) -> None:
    harness = Harness()
    harness.inspection = inspection

    result = harness.workflow().execute(
        "source",
        "edit-1",
        "move_element",
        {"target": "hero photo", "dx_points": 2, "dy_points": 0},
        required_values=("703 Perception Way, Aberdeen, MD 21001",),
    )

    assert not result.verified
    assert phrase in result.message
    assert "https" not in result.message
    assert "Open the updated flyer" not in result.message


def test_an_unintended_second_leaf_change_stops_before_rendering() -> None:
    harness = Harness()
    harness.change_extra_leaf = True

    result = harness.workflow().execute(
        "source",
        "edit-1",
        "set_font_size",
        {"target": "price", "points": 24},
        field_values={"price": ("$615,000",)},
    )

    assert not result.verified
    assert "only the requested flyer element changed" in result.message
    assert harness.rendered == []


def test_a_source_change_between_resolution_and_copy_stops_before_mutation() -> None:
    harness = Harness()

    def stale_copy(source_id: str, edit_id: str) -> tuple[str, str]:
        file_id, url = harness.copy(source_id, edit_id)
        assert harness.draft is not None
        harness.draft["slides"][0]["pageElements"][0]["transform"]["translateX"] = 50
        return file_id, url

    workflow = EditWorkflow(
        stale_copy, harness.read, harness.apply, harness.thumbnail, harness.look
    )
    result = workflow.execute(
        "source",
        "edit-1",
        "set_font_size",
        {"target": "price", "points": 24},
        field_values={"price": ("$615,000",)},
    )

    assert not result.verified
    assert "changed while" in result.message
    assert harness.applied == []


def test_a_text_correction_autofits_the_changed_box_before_vision() -> None:
    harness = Harness(_presentation(address_width=3_000_000))
    replacement = "703 Perception Way Extended, Aberdeen, MD 21001"

    result = harness.workflow().execute(
        "source",
        "edit-1",
        "correct_field",
        {
            "current": "703 Perception Way, Aberdeen, MD 21001",
            "replacement": replacement,
        },
        field_values={"address": ("703 Perception Way, Aberdeen, MD 21001",)},
        required_values=("703 Perception Way, Aberdeen, MD 21001", "410.456.3564"),
    )

    assert result.verified
    assert len(harness.applied) == 2
    fit_request = harness.applied[1][0]["updateTextStyle"]
    assert fit_request["objectId"] == "address"
    assert fit_request["style"]["fontSize"]["magnitude"] < 24


def test_an_unreadable_corrected_value_is_not_linked() -> None:
    harness = Harness(_presentation(address_width=250_000))

    result = harness.workflow().execute(
        "source",
        "edit-1",
        "correct_field",
        {
            "current": "703 Perception Way, Aberdeen, MD 21001",
            "replacement": (
                "703 Perception Way With An Impossibly Long Address Extension, "
                "Aberdeen, Maryland 21001"
            ),
        },
        field_values={"address": ("703 Perception Way, Aberdeen, MD 21001",)},
    )

    assert not result.verified
    assert "readability limit" in result.message
    assert harness.rendered == []


def test_an_explicit_font_size_that_overflows_is_not_silently_reduced() -> None:
    harness = Harness(_presentation(address_width=2_000_000))

    result = harness.workflow().execute(
        "source",
        "edit-1",
        "set_font_size",
        {"target": "address", "points": 80},
        field_values={"address": ("703 Perception Way, Aberdeen, MD 21001",)},
    )

    assert not result.verified
    assert "readability limit" in result.message or "would not fit" in result.message
    assert harness.rendered == []
