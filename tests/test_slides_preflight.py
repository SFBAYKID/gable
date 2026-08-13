"""Measured template triage runs before any flyer copy is created."""

from __future__ import annotations

from typing import Any

from gable.slackapp.style import violations
from gable.slides import fields, fitting, preflight


def _text(object_id: str, value: str, width_pt: float, font_pt: float = 20) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": width_pt * fitting.EMU_PER_POINT},
            "height": {"magnitude": 30 * fitting.EMU_PER_POINT},
        },
        "transform": {"scaleX": 1, "scaleY": 1},
        "shape": {
            "shapeType": "TEXT_BOX",
            "text": {
                "textElements": [
                    {
                        "textRun": {
                            "content": value,
                            "style": {"fontSize": {"magnitude": font_pt, "unit": "PT"}},
                        }
                    }
                ]
            },
        },
    }


def _hero() -> dict[str, Any]:
    return {
        "objectId": "photo-frame",
        "size": {
            "width": {"magnitude": 8_000_000},
            "height": {"magnitude": 6_000_000},
        },
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 0, "translateY": 0},
        "shape": {"shapeProperties": {"shapeBackgroundFill": {}}},
    }


def _presentation(*elements: dict[str, Any]) -> dict[str, Any]:
    return {
        "pageSize": {
            "width": {"magnitude": 10_000_000},
            "height": {"magnitude": 12_500_000},
        },
        "slides": [{"objectId": "page-1", "pageElements": [*elements, _hero()]}],
    }


def _analyze(presentation: dict[str, Any], values: dict[str, str]) -> preflight.Report:
    text = [
        element.get("shape", {})
        .get("text", {})
        .get("textElements", [{}])[0]
        .get("textRun", {})
        .get("content", "")
        for element in presentation["slides"][0]["pageElements"]
    ]
    resolution = fields.resolve([item for item in text if item])
    return preflight.analyze(
        presentation,
        "New Listing",
        "New Listing",
        resolution,
        values,
    )


def test_actual_long_email_is_flagged_with_a_measured_width_before_build() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _text("email", "Email", 300),
    )
    report = _analyze(
        presentation,
        {
            "address": "123 Main St, Baltimore, MD 21201",
            "agent_email": "a.very.long.agent.address@cornerhouserealty.com",
        },
    )

    issue = next(item for item in report.warnings if item.code == "tight_agent_email")
    assert "characters" in issue.say
    assert "percent more room" in issue.say
    assert not violations(issue.say)


def test_text_that_would_become_unreadable_cannot_be_overridden() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _text("email", "Email", 55),
    )
    report = _analyze(
        presentation,
        {
            "address": "123 Main St, Baltimore, MD 21201",
            "agent_email": "a.very.long.agent.address@cornerhouserealty.com",
        },
    )

    assert any(issue.code == "unreadable_agent_email" for issue in report.blockers)


def test_a_recognised_field_without_a_value_pauses_before_a_copy() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _text("social", "[SOCIAL]", 200),
    )

    report = _analyze(
        presentation,
        {"address": "123 Main St, Baltimore, MD 21201"},
    )

    issue = next(item for item in report.blockers if item.code == "missing_value_social_handle")
    assert issue.status == "needs_info"
    assert "social handle" in issue.say
    assert "remove that section" in issue.say
    assert not violations(issue.say)


def test_a_material_photo_crop_is_a_prebuild_photo_question() -> None:
    presentation = _presentation(_text("address", "[PROPERTY ADDRESS]", 500))
    text = ["[PROPERTY ADDRESS]"]
    report = preflight.analyze(
        presentation,
        "New Listing",
        "New Listing",
        fields.resolve(text),
        {"address": "123 Main St, Baltimore, MD 21201"},
        photo_size=(800, 1200),
    )

    issue = next(item for item in report.warnings if item.code == "large_photo_crop")
    assert issue.status == "needs_photo"
    assert "outside the frame" in issue.say


def test_an_unknown_placeholder_and_missing_frame_are_structural_blockers() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _text("unknown", "[MLS NUMBER]", 200),
    )
    report = _analyze(
        presentation,
        {"address": "123 Main St, Baltimore, MD 21201"},
    )
    assert any(issue.code == "unknown_placeholder" for issue in report.blockers)

    presentation["slides"][0]["pageElements"].pop()
    without_frame = _analyze(
        presentation,
        {"address": "123 Main St, Baltimore, MD 21201"},
    )
    assert any(issue.code == "missing_photo_frame" for issue in without_frame.blockers)


def test_a_multi_slide_template_is_refused_before_only_one_page_can_be_checked() -> None:
    presentation = _presentation(_text("address", "[PROPERTY ADDRESS]", 500))
    presentation["slides"].append({"objectId": "page-2", "pageElements": []})
    report = _analyze(presentation, {"address": "123 Main St, Baltimore, MD 21201"})
    assert report.blockers[0].code == "slide_count"


def test_a_new_template_gets_a_capacity_check_before_any_listing_uses_it() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        _text("email", "Email", 100),
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    issue = next(item for item in report.warnings if item.code == "capacity_agent_email")
    assert "roughly" in issue.say
    assert "safe template test" in issue.say
    assert "updated template" in issue.say
    assert not violations(issue.say)


def test_a_tall_name_box_is_not_treated_as_permission_to_wrap_the_name() -> None:
    name = _text("name", "AGENT NAME", 120)
    name["size"]["height"]["magnitude"] = 90 * fitting.EMU_PER_POINT
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        name,
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    issue = next(item for item in report.warnings if item.code == "capacity_agent_name")
    assert "roughly" in issue.say
    assert "making that section wider" in issue.say


def test_a_grouped_fillable_field_is_refused_instead_of_mismeasured() -> None:
    child = _text("email", "Email", 200)
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        {
            "objectId": "group",
            "transform": {"scaleX": 0.5, "scaleY": 1},
            "elementGroup": {"children": [child]},
        },
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    issue = next(item for item in report.blockers if item.code == "grouped_agent_email")
    assert "inside grouped artwork" in issue.say
    assert "own text box" in issue.say
