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


def _image_headshot(object_id: str = "sample-portrait") -> dict[str, Any]:
    """A sample agent already embedded as a Slides image object."""
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": 1_500_000},
            "height": {"magnitude": 1_800_000},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": 8_000_000,
            "translateY": 10_000_000,
        },
        "image": {"contentUrl": "https://slides.example/sample-agent.jpg"},
    }


def _agent_card() -> tuple[dict[str, Any], dict[str, Any]]:
    """Recognised name and phone fields beside the portrait candidate."""
    name = _text("agent-name", "AGENT NAME", 175)
    name["transform"].update({"translateX": 5_300_000, "translateY": 10_100_000})
    phone = _text("agent-phone", "Phone", 140)
    phone["transform"].update({"translateX": 5_300_000, "translateY": 10_800_000})
    return name, phone


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


def test_small_source_containment_does_not_claim_a_crop() -> None:
    """A tiny tall upload stays whole rather than losing its hypothetical cover crop."""
    presentation = _presentation(_text("address", "[PROPERTY ADDRESS]", 500))
    report = preflight.analyze(
        presentation,
        "New Listing",
        "New Listing",
        fields.resolve(["[PROPERTY ADDRESS]"]),
        {"address": "123 Main St, Baltimore, MD 21201"},
        photo_size=(100, 200),
    )

    assert not any(issue.code == "large_photo_crop" for issue in report.warnings)


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


def test_a_new_template_that_cannot_fit_readable_content_is_reported_not_blocked() -> None:
    """The allowance asks about a value nobody has yet, so it advises.

    Every real value is measured exactly against the same box before a flyer is
    built. Blocking on the estimate stopped every listing on a design the moment
    Carmen edited it.
    """
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        _text("email", "Email", 100),
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    issue = next(item for item in report.warnings if item.code == "capacity_agent_email")
    assert report.blockers == ()
    assert "safe test" in issue.say
    assert "readability limit" in issue.say
    assert "fit each real value" in issue.say
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
    assert "readability limit" in issue.say
    assert "Widen that section if you can" in issue.say


def test_new_template_capacity_that_can_autofit_is_not_a_warning() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        _text("email", "Email", 200),
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    assert not any(issue.code == "capacity_agent_email" for issue in report.issues)


def test_a_grouped_fillable_field_is_measured_at_its_rendered_width() -> None:
    """A child's transform is relative to its group, so composing matters.

    This used to be refused outright rather than measured, which rejected New
    Listing with Open House every time: it scales its REALTOR box to 0.75, so
    its own numbers overstated the usable width by a third.
    """
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
    boxes = {box.object_id: box for box in preflight.text_boxes(presentation)}

    assert not any(item.code.startswith("grouped_") for item in report.blockers)
    own_width = child["size"]["width"]["magnitude"] * child["transform"]["scaleX"]
    assert boxes["email"].width_emu == own_width * 0.5, "the group's scale must apply"


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


def test_an_existing_slides_image_is_a_required_headshot_slot() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        *_agent_card(),
        _image_headshot(),
    )

    report = _analyze(
        presentation,
        {"address": "703 Perception Way, Aberdeen, MD 21001", "headshot": ""},
    )

    issue = next(item for item in report.blockers if item.code == "missing_headshot")
    assert "Head Shots" in issue.say


def test_an_image_and_shape_portrait_pair_is_ambiguous() -> None:
    image = _image_headshot()
    image["transform"]["translateX"] = 5_500_000
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        *_agent_card(),
        _headshot(),
        image,
    )

    report = preflight.certify(presentation, "New Listing", "Just Listed")

    assert any(issue.code == "ambiguous_headshot_frame" for issue in report.blockers)


def test_square_logo_away_from_agent_card_is_not_a_headshot() -> None:
    logo = _image_headshot("brokerage-logo")
    logo["transform"].update({"translateX": 300_000, "translateY": 500_000})
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        *_agent_card(),
        logo,
    )

    report = _analyze(
        presentation,
        {"address": "703 Perception Way, Aberdeen, MD 21001", "headshot": ""},
    )

    assert not any(issue.code == "missing_headshot" for issue in report.blockers)


def test_qr_code_without_resolved_agent_fields_is_not_a_headshot() -> None:
    qr_code = _image_headshot("listing-qr-code")
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        qr_code,
    )

    report = _analyze(
        presentation,
        {"address": "703 Perception Way, Aberdeen, MD 21001", "headshot": ""},
    )

    assert not any(issue.code == "missing_headshot" for issue in report.blockers)


def test_secondary_property_image_away_from_contact_card_is_not_a_headshot() -> None:
    secondary = _image_headshot("secondary-property-photo")
    secondary["size"] = {
        "width": {"magnitude": 3_000_000},
        "height": {"magnitude": 2_000_000},
    }
    secondary["transform"].update({"translateX": 500_000, "translateY": 6_500_000})
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 900),
        *_agent_card(),
        secondary,
    )

    report = _analyze(
        presentation,
        {"address": "703 Perception Way, Aberdeen, MD 21001", "headshot": ""},
    )

    assert not any(issue.code == "missing_headshot" for issue in report.blockers)


def test_releasing_blank_values_frees_only_the_missing_value_blockers() -> None:
    """Chase's rule: a value nobody has is Carmen's decision, not a dead end.

    She either supplies it or says to build and fills it in herself. That
    release must not quietly wave through an unreadable type size or an unsafe
    structure, which are not values and are not hers to accept.
    """
    report = preflight.Report(
        issues=(
            preflight.Issue("missing_value_price", "no price", blocking=True),
            preflight.Issue("missing_value_square_feet", "no sqft", blocking=True),
            preflight.Issue("unreadable_agent_name", "6pt type", blocking=True),
            preflight.Issue("ambiguous_headshot_frame", "two wells", blocking=True),
            preflight.Issue("photo_crop", "crops 31%", blocking=False),
        )
    )

    assert len(preflight.blocking_after_release(report, allow_blank_fields=False)) == 4

    released = preflight.blocking_after_release(report, allow_blank_fields=True)

    assert [issue.code for issue in released] == [
        "unreadable_agent_name",
        "ambiguous_headshot_frame",
    ]


def test_a_group_scales_the_type_but_a_box_transform_only_shapes_the_box() -> None:
    """The real New Listing with Open House title, measured 2026-08-14.

    It is stored as a 3,000,000 EMU square scaled to 1.11 x 0.13, inside a group
    scaled 0.75, and declared at 18.79pt. Multiplying both scales into the font
    read it as 1.79pt and refused the design as unreadable; the box transform
    shapes the box, and only the group scales the type.
    """
    title = {
        "objectId": "p1_i103",
        "shape": {
            "text": {
                "textElements": [
                    {
                        "textRun": {
                            "content": "REALTOR",
                            "style": {"fontSize": {"magnitude": 18.79, "unit": "PT"}},
                        }
                    }
                ]
            }
        },
        "size": {"width": {"magnitude": 3000000}, "height": {"magnitude": 3000000}},
        "transform": {"scaleX": 1.1123613333333333, "scaleY": 0.12682566666666667},
    }
    presentation = {
        "slides": [
            {
                "objectId": "p1",
                "pageElements": [
                    {
                        "objectId": "group",
                        "transform": {"scaleX": 0.75, "scaleY": 0.75},
                        "elementGroup": {"children": [title]},
                    }
                ],
            }
        ]
    }

    box = next(item for item in preflight.text_boxes(presentation) if item.object_id == "p1_i103")

    assert round(box.font_size_pt, 2) == 14.09
    assert box.font_size_pt > fitting.MIN_READABLE_PT
    assert round(box.width_emu) == 2502813


def test_a_title_no_design_has_room_for_falls_back_to_its_credential() -> None:
    # Under Contract's live title slot: 80pt wide, 32pt type. Sara Wolz's proven
    # title needs roughly seven times that width, and until 2026-08-14 the run
    # stopped dead asking Chase to redraw the design.
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 430, font_pt=20),
        _text("title", "Realtor", 80, font_pt=32),
    )

    report = _analyze(
        presentation,
        {
            "address": "498 Old Mill Rd, Millersville, MD 21108",
            "agent_title": "Listing Manager, Transaction Coordinator & Realtor",
        },
    )

    assert report.blockers == ()
    assert report.adjusted == {"agent_title": "Realtor"}
    assert [issue.advisory for issue in report.warnings] == [
        "This design's title line has room for one word, so it says Realtor rather "
        "than the full Listing Manager, Transaction Coordinator & Realtor."
    ]


def test_a_title_that_fits_is_left_exactly_as_the_profile_states_it() -> None:
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 430, font_pt=20),
        _text("title", "Realtor", 400, font_pt=12),
    )

    report = _analyze(
        presentation,
        {
            "address": "498 Old Mill Rd, Millersville, MD 21108",
            "agent_title": "Listing Manager, Transaction Coordinator & Realtor",
        },
    )

    assert report.adjusted == {}
    assert report.issues == ()


def test_a_title_holding_no_credential_is_never_replaced_by_one() -> None:
    # Inventing REALTOR for somebody whose profile does not claim it is a false
    # statement about their licence, so a title Gable cannot shorten truthfully
    # remains a stop.
    presentation = _presentation(
        _text("address", "[PROPERTY ADDRESS]", 430, font_pt=20),
        _text("title", "Realtor", 80, font_pt=32),
    )

    report = _analyze(
        presentation,
        {
            "address": "498 Old Mill Rd, Millersville, MD 21108",
            "agent_title": "Associate Broker and Transaction Coordinator",
        },
    )

    assert report.adjusted == {}
    assert [issue.code for issue in report.blockers] == ["unreadable_agent_title"]


def test_a_count_the_design_writes_on_two_lines_is_measured_on_two() -> None:
    r"""New Listing draws its counts as "4\nBedrooms"."""
    box = _text("beds", "4\nBedrooms", 100, font_pt=20)
    presentation = _presentation(_text("address", "[PROPERTY ADDRESS]", 900), box)

    report = _analyze(presentation, {"address": "1 Main St, Baltimore, MD 21201", "beds": "3"})

    assert [issue.code for issue in report.blockers] == []


def test_a_name_is_still_refused_the_second_line_it_would_wrap_onto() -> None:
    """Annie Nowicki's name wrapped onto the Realtor title beneath it."""
    name = _text("name", "AGENT NAME", 60, font_pt=30)
    name["size"]["height"]["magnitude"] = 90 * fitting.EMU_PER_POINT
    presentation = _presentation(_text("address", "[PROPERTY ADDRESS]", 900), name)

    report = _analyze(
        presentation,
        {"address": "1 Main St, Baltimore, MD 21201", "agent_name": "Bartholomew Fitzwilliam"},
    )

    assert [issue.code for issue in report.blockers] == ["unreadable_agent_name"]


def test_a_design_box_that_would_be_blanked_is_asked_about_first() -> None:
    """Row 16's open house reads "7/11/2026" — a date with no time.

    Open House sets the date and the time in separate boxes, so the date box
    filled and the time box was blanked, and the flyer showed the design's own
    two separators with a gap between them. The visual gate refused it, which
    cost a round trip for something knowable before the copy was ever made.
    """
    presentation = _presentation(
        _text("date", "Sunday, Aug 2, 2026", 300),
        _text("time", "2-4PM", 120),
        _text("price", "$1,199,000", 200),
    )

    report = _analyze(presentation, {"open_house": "7/11/2026", "price": "$500,000"})

    asked = [issue for issue in report.issues if issue.code == "missing_part_open_house"]
    assert asked, [issue.code for issue in report.issues]
    assert asked[0].status == "needs_info"
    assert "open house" in asked[0].say


def test_an_open_house_carrying_its_time_is_not_asked_again() -> None:
    presentation = _presentation(
        _text("date", "Sunday, Aug 2, 2026", 300),
        _text("time", "2-4PM", 120),
        _text("price", "$1,199,000", 200),
    )

    report = _analyze(
        presentation,
        {"open_house": "Sunday, Sep 6, 2026 1-3PM", "price": "$500,000"},
    )

    assert not [issue for issue in report.issues if issue.code.startswith("missing_part_")]
