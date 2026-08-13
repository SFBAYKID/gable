"""Pure target resolution and exact-change proof for conversational edits."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from gable.slides.edit_plan import (
    EditPlanError,
    batch_was_confirmed,
    plan_edit,
    presentation_changed_only_as_planned,
)


def _text(object_id: str, value: str, points: float = 20) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "shape": {
            "text": {
                "textElements": [
                    {
                        "textRun": {
                            "content": value,
                            "style": {"fontSize": {"magnitude": points, "unit": "PT"}},
                        }
                    }
                ]
            }
        },
        "size": {
            "width": {"magnitude": 2_000_000, "unit": "EMU"},
            "height": {"magnitude": 500_000, "unit": "EMU"},
        },
        "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"},
    }


def _image(object_id: str) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "image": {"contentUrl": "https://example.test/source.jpg"},
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


def _presentation(*extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    _text("address", "703 Perception Way, Aberdeen, MD 21001", 24),
                    _text("price", "$615,000", 30),
                    _image("gableHero_source"),
                    _image("gableFace_source"),
                    *extra,
                ],
            }
        ]
    }


def test_font_size_resolves_a_semantic_field_to_one_object() -> None:
    plan = plan_edit(
        _presentation(),
        "set_font_size",
        {"target": "address", "points": 20},
        {"address": ("703 Perception Way, Aberdeen, MD 21001",)},
    )

    assert plan.target_object_id == "address"
    assert plan.requests[0]["updateTextStyle"]["style"]["fontSize"]["magnitude"] == 20
    assert plan.dynamic_text == "703 Perception Way, Aberdeen, MD 21001"


@pytest.mark.parametrize(
    ("tool", "arguments", "request_name", "target_id"),
    [
        (
            "resize_photo",
            {"which": "hero", "factor": 1.1},
            "updatePageElementTransform",
            "gableHero_source",
        ),
        (
            "move_element",
            {"target": "headshot", "dx_points": 2, "dy_points": -3},
            "updatePageElementTransform",
            "gableFace_source",
        ),
        (
            "correct_field",
            {"current": "$615,000", "replacement": "$620,000"},
            "replaceAllText",
            "price",
        ),
    ],
)
def test_supported_edits_name_only_the_intended_leaf(
    tool: str,
    arguments: dict[str, Any],
    request_name: str,
    target_id: str,
) -> None:
    plan = plan_edit(_presentation(), tool, arguments)

    assert plan.target_object_id == target_id
    assert list(plan.requests[0]) == [request_name]


def test_a_line_colour_uses_the_line_request_not_shape_properties() -> None:
    line = {
        "objectId": "divider",
        "line": {},
        "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"},
    }

    plan = plan_edit(
        _presentation(line),
        "set_colour",
        {"target": "middle line", "colour": "navy"},
    )

    assert plan.target_object_id == "divider"
    assert "updateLineProperties" in plan.requests[0]


def test_an_ambiguous_value_is_never_ranked() -> None:
    with pytest.raises(EditPlanError, match="exactly one price"):
        plan_edit(
            _presentation(_text("second-price", "$615,000")),
            "set_font_size",
            {"target": "price", "points": 20},
            {"price": ("$615,000",)},
        )


def test_images_require_gables_inserted_semantic_ids() -> None:
    presentation = _presentation(_image("company-logo"))
    presentation["slides"][0]["pageElements"] = [
        element
        for element in presentation["slides"][0]["pageElements"]
        if element["objectId"] != "gableFace_source"
    ]

    with pytest.raises(EditPlanError, match="exactly one headshot"):
        plan_edit(presentation, "resize_photo", {"which": "headshot", "factor": 1.1})


def test_batch_confirmation_requires_every_reply_and_one_literal_match() -> None:
    plan = plan_edit(
        _presentation(),
        "correct_field",
        {"current": "$615,000", "replacement": "$620,000"},
    )

    assert batch_was_confirmed(
        plan,
        {"replies": [{"replaceAllText": {"occurrencesChanged": 1}}]},
    )
    assert not batch_was_confirmed(plan, {"replies": []})
    assert not batch_was_confirmed(
        plan,
        {"replies": [{"replaceAllText": {"occurrencesChanged": 2}}]},
    )


def test_readback_proves_only_the_planned_leaf_changed() -> None:
    before = _presentation()
    plan = plan_edit(
        before,
        "correct_field",
        {"current": "$615,000", "replacement": "$620,000"},
    )
    after = deepcopy(before)
    after["slides"][0]["pageElements"][1]["shape"]["text"]["textElements"][0]["textRun"][
        "content"
    ] = "$620,000"

    assert presentation_changed_only_as_planned(before, after, plan)

    after["slides"][0]["pageElements"][0]["transform"]["translateX"] = 50
    assert not presentation_changed_only_as_planned(before, after, plan)


def test_readback_rejects_an_acknowledged_no_op() -> None:
    before = _presentation()
    plan = plan_edit(
        before,
        "move_element",
        {"target": "hero photo", "dx_points": 2, "dy_points": 0},
    )

    assert not presentation_changed_only_as_planned(before, deepcopy(before), plan)


def test_expiring_google_image_urls_are_not_mistaken_for_an_extra_edit() -> None:
    before = _presentation()
    plan = plan_edit(
        before,
        "correct_field",
        {"current": "$615,000", "replacement": "$620,000"},
    )
    after = deepcopy(before)
    after["slides"][0]["pageElements"][1]["shape"]["text"]["textElements"][0]["textRun"][
        "content"
    ] = "$620,000"
    after["slides"][0]["pageElements"][2]["image"]["contentUrl"] = (
        "https://example.test/refreshed-signed-url"
    )

    assert presentation_changed_only_as_planned(before, after, plan)
