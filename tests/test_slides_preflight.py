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


def _headshot() -> dict[str, Any]:
    """A separate empty square near the lower-right agent card."""
    return {
        "objectId": "headshot-frame",
        "size": {
            "width": {"magnitude": 1_500_000},
            "height": {"magnitude": 1_500_000},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": 8_000_000,
            "translateY": 10_000_000,
        },
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


def test_actual_long_email_that_can_autofit_is_not_a_human_warning() -> None:
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

    assert report.issues == ()


def test_mike_sold_address_and_name_can_autofit_without_a_pause() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 430, font_pt=20),
        _text("name", "AGENT NAME", 125, font_pt=20),
    )
    report = _analyze(
        presentation,
        {
            "address": "703 Perception Way, Aberdeen, MD 21001",
            "agent_name": "Mike Kulnich",
        },
    )

    assert report.blockers == ()
    assert report.warnings == ()


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


def test_dynamic_text_already_below_the_readability_limit_blocks_even_when_it_fits() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _text("name", "AGENT NAME", 500, font_pt=6),
    )
    report = _analyze(
        presentation,
        {
            "address": "123 Main St, Baltimore, MD 21201",
            "agent_name": "Mike Kulnich",
        },
    )

    issue = next(item for item in report.blockers if item.code == "unreadable_agent_name")
    assert "already 6 points" in issue.say
    assert "readability limit" in issue.say
    assert not violations(issue.say)


def test_a_bare_realtor_credential_without_authoritative_data_blocks_the_sold_design() -> None:
    presentation = _presentation(
        _text("address", "32 S Prospect Ave Baltimore, MD 21228", 500),
        _text("title", "Realtor", 200),
    )

    report = _analyze(
        presentation,
        {
            "address": "703 Perception Way, Aberdeen, MD 21001",
            "agent_name": "Mike Kulnich",
            "agent_title": "",
        },
    )

    issue = next(item for item in report.blockers if item.code == "missing_value_agent_title")
    assert issue.status == "needs_info"
    assert "agent title" in issue.say
    assert not violations(issue.say)


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


def test_a_sample_headshot_can_never_survive_for_an_agent_without_a_file() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _headshot(),
    )

    report = _analyze(
        presentation,
        {
            "address": "123 Main St, Baltimore, MD 21201",
            "agent_name": "Jane Doe",
            "headshot": "",
        },
    )

    issue = next(item for item in report.blockers if item.code == "missing_headshot")
    assert issue.status == "needs_info"
    assert "Jane Doe" in issue.say
    assert "Head Shots" in issue.say
    assert not violations(issue.say)


def test_a_material_photo_crop_is_a_postbuild_advisory_not_a_question() -> None:
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
    assert not issue.blocking
    assert "center-cropped and fitted" in issue.advisory
    assert "outside that frame" in issue.say
    assert "?" not in issue.say
    assert "run anyway" not in issue.say.casefold()


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


def test_a_bare_unknown_field_label_is_not_mistaken_for_brand_copy() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 500),
        _text("unknown", "MLS NUMBER", 200),
    )

    report = _analyze(
        presentation,
        {"address": "123 Main St, Baltimore, MD 21201"},
    )

    issue = next(item for item in report.blockers if item.code == "unknown_placeholder")
    assert "MLS NUMBER" in issue.say
    assert not violations(issue.say)


def test_a_multi_slide_template_is_refused_before_only_one_page_can_be_checked() -> None:
    presentation = _presentation(_text("address", "[PROPERTY ADDRESS]", 500))
    presentation["slides"].append({"objectId": "page-2", "pageElements": []})
    report = _analyze(presentation, {"address": "123 Main St, Baltimore, MD 21201"})
    assert report.blockers[0].code == "slide_count"


def test_a_new_template_that_cannot_fit_readable_content_is_blocked() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        _text("email", "Email", 100),
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    issue = next(item for item in report.blockers if item.code == "capacity_agent_email")
    assert "safe test" in issue.say
    assert "readability limit" in issue.say
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

    issue = next(item for item in report.blockers if item.code == "capacity_agent_name")
    assert "readability limit" in issue.say
    assert "Widen that section" in issue.say


def test_new_template_capacity_that_can_autofit_is_not_a_warning() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        _text("email", "Email", 200),
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    assert not any(issue.code == "capacity_agent_email" for issue in report.issues)


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


def test_a_rotated_fillable_field_is_refused_instead_of_measured_as_flat() -> None:
    address = _text("address", "[PROPERTY ADDRESS]", 900)
    address["transform"]["shearX"] = 0.25
    presentation = _presentation(address)

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    issue = next(item for item in report.blockers if item.code == "unsupported_transform_address")
    assert "rotated, skewed, or mirrored" in issue.say


def test_two_separate_hero_candidates_are_refused_instead_of_taking_the_largest() -> None:
    second = _hero()
    second["objectId"] = "second-photo-frame"
    second["size"]["width"]["magnitude"] = 7_000_000
    second["transform"]["translateY"] = 4_000_000
    presentation = _presentation(_text("address", "[PROPERTY ADDRESS]", 900), second)

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    assert any(issue.code == "missing_photo_frame" for issue in report.blockers)


def test_two_headshot_candidates_are_refused_instead_of_choosing_a_face() -> None:
    first = _headshot()
    second = _headshot()
    second["objectId"] = "second-headshot-frame"
    second["transform"]["translateX"] = 6_000_000
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        first,
        second,
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    assert any(issue.code == "ambiguous_headshot_frame" for issue in report.blockers)
