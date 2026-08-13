"""The visual delivery gate uses the current Responses API without silent passes."""

from __future__ import annotations

from typing import Any

import pytest

from gable.pipeline import vision


def _completed(text: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def test_inspection_uses_original_detail_and_strict_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def post(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        seen.update(payload)
        assert api_key == "test-key"
        return _completed(
            '{"looks_right":true,"confident":true,"problems":[],"problem_kinds":[],'
            '"remedy":"none",'
            '"source_conflict_visible":false}'
        )

    monkeypatch.setattr(vision, "_post", post)
    result = vision.inspect(b"image bytes", api_key="test-key", model="gpt-5.6-sol")

    assert result.looks_right and result.confident and result.checked
    assert seen["model"] == "gpt-5.6-sol"
    image = seen["input"][0]["content"][1]
    assert image["type"] == "input_image"
    assert image["detail"] == "original"
    assert image["image_url"].startswith("data:image/png;base64,")
    assert seen["reasoning"] == {"effort": "high"}
    assert seen["text"]["format"]["type"] == "json_schema"
    assert seen["text"]["format"]["strict"] is True
    schema = seen["text"]["format"]["schema"]
    assert schema["properties"]["remedy"]["enum"] == ["none", "review", "replace_photo"]
    assert schema["properties"]["source_conflict_visible"] == {"type": "boolean"}
    assert "source_photo_conflict" in schema["properties"]["problem_kinds"]["items"]["enum"]
    assert "problem_kinds" in schema["required"]
    assert "source_conflict_visible" in schema["required"]
    assert "too small to read" in seen["input"][0]["content"][0]["text"]


def test_an_incomplete_response_never_degrades_to_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vision, "_post", lambda _payload, _key: {"status": "incomplete"})

    result = vision.inspect(b"image bytes", api_key="test-key")

    assert result.checked is False
    assert result.looks_right is False


def test_split_response_text_is_joined_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vision,
        "_post",
        lambda _payload, _key: {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": '{"looks_right":true,'},
                        {
                            "type": "output_text",
                            "text": (
                                '"confident":true,"problems":[],"problem_kinds":[],'
                                '"remedy":"none",'
                                '"source_conflict_visible":false}'
                            ),
                        },
                    ],
                }
            ],
        },
    )

    result = vision.inspect(b"image", api_key="test-key")

    assert result.looks_right and result.confident and result.checked


def test_source_photo_and_render_are_compared_in_one_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def post(payload: dict[str, Any], _api_key: str) -> dict[str, Any]:
        seen.update(payload)
        return _completed(
            '{"looks_right":true,"confident":true,"problems":[],"problem_kinds":[],'
            '"remedy":"none",'
            '"source_conflict_visible":false}'
        )

    monkeypatch.setattr(vision, "_post", post)

    result = vision.inspect(
        b"rendered flyer",
        api_key="test-key",
        reference_image_bytes=b"human source",
    )

    assert result.looks_right and result.confident and result.checked
    content = seen["input"][0]["content"]
    assert [part["type"] for part in content] == [
        "input_text",
        "input_image",
        "input_image",
    ]
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,")
    assert content[2]["image_url"].startswith("data:image/png;base64,")
    assert content[1]["detail"] == content[2]["detail"] == "original"
    prompt = content[0]["text"]
    assert "independently legible in the FIRST image" in prompt
    assert "visible only in the second image" in prompt.lower()
    assert "blurred and darkened fill made from the same" in prompt
    assert "Do not report that intentional backdrop" in prompt


def test_template_inspection_uses_the_placeholder_aware_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def post(payload: dict[str, Any], _api_key: str) -> dict[str, Any]:
        seen.update(payload)
        return _completed(
            '{"looks_right":true,"confident":true,"problems":[],"problem_kinds":[],'
            '"remedy":"none",'
            '"source_conflict_visible":false}'
        )

    monkeypatch.setattr(vision, "_post", post)

    result = vision.inspect_template(b"template", api_key="test-key")

    assert result.looks_right and result.confident
    prompt = seen["input"][0]["content"][0]["text"]
    assert "Intentional placeholder wording" in prompt
    assert "inconsistent spacing" in prompt
    schema = seen["text"]["format"]["schema"]
    assert schema["properties"]["remedy"]["enum"] == ["none", "review"]
    assert schema["properties"]["source_conflict_visible"]["enum"] == [False]
    assert schema["properties"]["problem_kinds"]["items"]["enum"] == [
        "text",
        "layout",
        "placeholder",
        "other",
    ]


def test_a_visible_problem_is_preserved_for_the_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vision,
        "_post",
        lambda _payload, _key: _completed(
            '{"looks_right":false,"confident":true,'
            '"problems":["The address overlaps the divider line."],'
            '"problem_kinds":["layout"],"remedy":"review",'
            '"source_conflict_visible":false}'
        ),
    )

    result = vision.inspect(b"image bytes", api_key="test-key")

    assert result.checked is True
    assert result.looks_right is False
    assert result.problems == ["The address overlaps the divider line."]


def test_string_false_cannot_be_coerced_into_a_silent_pass() -> None:
    result = vision.parse(
        '{"looks_right":"false","confident":true,"problems":[],"problem_kinds":[],'
        '"remedy":"review",'
        '"source_conflict_visible":false}'
    )

    assert result.checked is False
    assert result.looks_right is False


def test_a_pass_that_also_names_a_problem_is_treated_as_a_failure() -> None:
    result = vision.parse(
        '{"looks_right":true,"confident":true,"problems":["The price is clipped."],'
        '"problem_kinds":["text"],'
        '"remedy":"none","source_conflict_visible":false}'
    )

    assert result.checked is True
    assert result.looks_right is False
    assert result.problems == ["The price is clipped."]


def test_a_contradictory_pass_cannot_authorize_replacing_the_source() -> None:
    result = vision.parse(
        '{"looks_right":true,"confident":true,'
        '"problems":["The source house number says 721."],'
        '"problem_kinds":["source_photo_conflict"],'
        '"remedy":"replace_photo","source_conflict_visible":true}',
        has_reference_photo=True,
    )

    assert result.checked is True
    assert result.looks_right is False
    assert result.remedy is vision.InspectionRemedy.REVIEW
    assert result.source_conflict_visible is False
    assert result.needs_source_replacement is False


def test_a_wrong_property_photo_has_a_typed_replacement_remedy() -> None:
    """A confident source-photo contradiction can route straight to upload."""
    result = vision.parse(
        '{"looks_right":false,"confident":true,'
        '"problems":["The flyer says 703, but the house number in the photo says 721."],'
        '"problem_kinds":["source_photo_conflict"],'
        '"remedy":"replace_photo","source_conflict_visible":true}',
        has_reference_photo=True,
    )

    assert result.checked is True
    assert result.remedy is vision.InspectionRemedy.REPLACE_PHOTO
    assert result.problem_kinds == (vision.InspectionProblemKind.SOURCE_PHOTO_CONFLICT,)
    assert result.source_conflict_visible is True


def test_a_number_visible_only_in_the_render_never_blames_the_source() -> None:
    """An enhancement-created detail stays review even if the model asks to replace."""
    result = vision.parse(
        '{"looks_right":false,"confident":true,'
        '"problems":["The rendered house number says 721 but it is unreadable in the source."],'
        '"problem_kinds":["photo_output"],'
        '"remedy":"replace_photo","source_conflict_visible":false}',
        has_reference_photo=True,
    )

    assert result.checked is True
    assert result.remedy is vision.InspectionRemedy.REVIEW
    assert result.source_conflict_visible is False


def test_a_source_conflict_claim_without_a_first_image_stays_review() -> None:
    result = vision.parse(
        '{"looks_right":false,"confident":true,'
        '"problems":["The property number conflicts with the flyer."],'
        '"problem_kinds":["source_photo_conflict"],'
        '"remedy":"replace_photo","source_conflict_visible":true}'
    )

    assert result.checked is True
    assert result.remedy is vision.InspectionRemedy.REVIEW
    assert result.source_conflict_visible is False


def test_a_mixed_visual_failure_cannot_request_a_replacement_photo() -> None:
    """One unrelated layout defect makes the typed remedy human review."""
    result = vision.parse(
        '{"looks_right":false,"confident":true,'
        '"problems":["The source house number says 721.",'
        '"The address overlaps the divider."],'
        '"problem_kinds":["source_photo_conflict","layout"],'
        '"remedy":"replace_photo","source_conflict_visible":true}',
        has_reference_photo=True,
    )

    assert result.checked is True
    assert result.remedy is vision.InspectionRemedy.REVIEW
    assert result.source_conflict_visible is False
    assert result.needs_source_replacement is False


def test_problem_messages_and_kinds_must_have_the_same_length() -> None:
    result = vision.parse(
        '{"looks_right":false,"confident":true,'
        '"problems":["The photo is cropped."],"problem_kinds":[],'
        '"remedy":"review","source_conflict_visible":false}',
        has_reference_photo=True,
    )

    assert result.checked is False


def test_an_empty_typed_problem_cannot_preserve_a_false_array_match() -> None:
    result = vision.parse(
        '{"looks_right":false,"confident":true,"problems":["   "],'
        '"problem_kinds":["source_photo_conflict"],"remedy":"replace_photo",'
        '"source_conflict_visible":true}',
        has_reference_photo=True,
    )

    assert result.checked is False
    assert result.needs_source_replacement is False


@pytest.mark.parametrize(
    "reply",
    [
        (
            '{"looks_right":true,"confident":true,"problems":[],"remedy":"review",'
            '"problem_kinds":[],'
            '"source_conflict_visible":false}'
        ),
        (
            '{"looks_right":false,"confident":true,"problems":[],"problem_kinds":[],'
            '"remedy":"none",'
            '"source_conflict_visible":false}'
        ),
        (
            '{"looks_right":false,"confident":false,'
            '"problems":["The property image may be wrong."],"remedy":"replace_photo",'
            '"problem_kinds":["source_photo_conflict"],'
            '"source_conflict_visible":true}'
        ),
    ],
)
def test_an_inconsistent_remedy_fails_closed(reply: str) -> None:
    result = vision.parse(reply)

    assert result.checked is False
