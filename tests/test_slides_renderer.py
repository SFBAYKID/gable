"""Tests for the Slides fill logic.

Entirely offline — the renderer is pure by design, so the whole fill behaviour
is verified before a service account exists. Weighted toward the cases that
would put something wrong on a flyer a client sees: a leftover `{{price}}`
token, a stretched photo, or an unusable image URL reaching Google.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gable.models import AgentProfile, Listing
from gable.slides.renderer import (
    HERO_PHOTO_TAG,
    MAX_IMAGE_URL_BYTES,
    PlaceholderMap,
    TemplateError,
    build_fill_requests,
    build_image_request,
    build_text_requests,
    find_placeholders,
    flyer_filename,
    unfilled_placeholders,
    validate_image_url,
)

MOMENT = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
PHOTO = "https://gable-photos.nyc3.digitaloceanspaces.com/123-anywhere.jpg"


def _listing(**overrides: object) -> Listing:
    base: dict[str, object] = {
        "response_row_id": "abc123",
        "submitted_at": MOMENT,
        "agent_email": "jane@brokerage.com",
        "agent_name": "Jane Doe",
        "address": "123 Anywhere St, Any City, ST 12345",
        "price_display": "$1,200,000",
        "agent_phone": "+15551234567",
    }
    base.update(overrides)
    return Listing(**base)  # type: ignore[arg-type]


# --- placeholder discovery --------------------------------------------------


def test_find_placeholders_extracts_names() -> None:
    assert find_placeholders("{{price}} for {{address}}") == {"price", "address"}


def test_find_placeholders_tolerates_inner_spacing() -> None:
    assert find_placeholders("{{ price }}") == {"price"}


def test_find_placeholders_ignores_plain_braces() -> None:
    assert find_placeholders("a {single} brace and {{real}}") == {"real"}


def test_unfilled_placeholders_reports_the_gap() -> None:
    """A template token with no data would print `{{mls_number}}` on a flyer."""
    placeholders = PlaceholderMap.from_listing(_listing())
    missing = unfilled_placeholders("{{price}} {{mls_number}}", placeholders)
    assert missing == {"mls_number"}


def test_hero_photo_is_not_reported_as_unfilled() -> None:
    """It is filled by an image request, not a text one."""
    placeholders = PlaceholderMap.from_listing(_listing())
    assert unfilled_placeholders("{{hero_photo}} {{price}}", placeholders) == set()


# --- placeholder values -----------------------------------------------------


def test_values_come_from_the_listing() -> None:
    values = PlaceholderMap.from_listing(_listing()).values
    assert values["price"] == "$1,200,000"
    assert values["address"] == "123 Anywhere St, Any City, ST 12345"
    assert values["agent_phone"] == "+15551234567"


def test_agents_tab_wins_on_display_name() -> None:
    """The form's name field is free text; the Agents tab is the authority."""
    agent = AgentProfile(agent_email="jane@brokerage.com", agent_name="Jane R. Doe")
    values = PlaceholderMap.from_listing(_listing(agent_name="jane"), agent).values
    assert values["agent_name"] == "Jane R. Doe"


def test_agent_name_falls_back_to_the_form_when_the_tab_is_blank() -> None:
    agent = AgentProfile(agent_email="jane@brokerage.com", agent_name="")
    values = PlaceholderMap.from_listing(_listing(), agent).values
    assert values["agent_name"] == "Jane Doe"


def test_missing_data_renders_blank_not_as_a_visible_token() -> None:
    """A blank on a flyer is survivable; `{{price}}` printed on one is not."""
    placeholders = PlaceholderMap.from_listing(_listing(price_display=""))
    assert placeholders.values["price"] == ""
    assert "price" in placeholders.values


def test_token_formatting() -> None:
    assert PlaceholderMap({}).token("price") == "{{price}}"


# --- text requests ----------------------------------------------------------


def test_one_request_per_placeholder() -> None:
    placeholders = PlaceholderMap({"price": "$1", "address": "here"})
    requests = build_text_requests(placeholders)
    assert len(requests) == 2
    assert {r["replaceAllText"]["containsText"]["text"] for r in requests} == {
        "{{price}}",
        "{{address}}",
    }


def test_text_matching_is_case_sensitive() -> None:
    """Case-insensitive matching could collide with body copy."""
    requests = build_text_requests(PlaceholderMap({"price": "$1"}))
    assert requests[0]["replaceAllText"]["containsText"]["matchCase"] is True


def test_text_requests_are_deterministic() -> None:
    """Sorted output keeps a diff of two runs readable."""
    placeholders = PlaceholderMap({"z": "1", "a": "2", "m": "3"})
    names = [r["replaceAllText"]["containsText"]["text"] for r in build_text_requests(placeholders)]
    assert names == ["{{a}}", "{{m}}", "{{z}}"]


# --- image request ----------------------------------------------------------


def test_image_request_uses_center_inside() -> None:
    """A stretched house, or one with its roofline cropped off, is not shippable."""
    request = build_image_request(PHOTO)
    assert request["replaceAllShapesWithImage"]["replaceMethod"] == "CENTER_INSIDE"


def test_image_request_targets_the_hero_tag() -> None:
    request = build_image_request(PHOTO)
    assert request["replaceAllShapesWithImage"]["containsText"]["text"] == HERO_PHOTO_TAG
    assert request["replaceAllShapesWithImage"]["imageUrl"] == PHOTO


def test_custom_tag_is_honored() -> None:
    request = build_image_request(PHOTO, "{{secondary_photo}}")
    assert request["replaceAllShapesWithImage"]["containsText"]["text"] == "{{secondary_photo}}"


# --- URL validation ---------------------------------------------------------


def test_valid_https_jpeg_passes() -> None:
    validate_image_url(PHOTO)


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg", ".gif"])
def test_every_slides_supported_format_passes(suffix: str) -> None:
    validate_image_url(f"https://example.com/photo{suffix}")


def test_query_string_does_not_defeat_the_format_check() -> None:
    validate_image_url("https://example.com/photo.jpg?v=2&sig=abc")


def test_empty_url_rejected() -> None:
    with pytest.raises(TemplateError, match="empty"):
        validate_image_url("")


def test_http_rejected() -> None:
    """Slides requires a publicly accessible URL; http leaks it for no benefit."""
    with pytest.raises(TemplateError, match="https"):
        validate_image_url("http://example.com/photo.jpg")


def test_url_over_the_slides_two_kilobyte_limit_rejected() -> None:
    long_url = "https://example.com/" + "a" * MAX_IMAGE_URL_BYTES + ".jpg"
    with pytest.raises(TemplateError, match="2048"):
        validate_image_url(long_url)


def test_unsupported_format_rejected() -> None:
    """Slides takes PNG/JPEG/GIF only — webp would fail at Google, opaquely."""
    with pytest.raises(TemplateError, match="PNG"):
        validate_image_url("https://example.com/photo.webp")


def test_bad_url_never_reaches_google() -> None:
    with pytest.raises(TemplateError):
        build_image_request("https://example.com/photo.webp")


# --- full fill --------------------------------------------------------------


def test_fill_includes_text_and_image() -> None:
    requests = build_fill_requests(_listing(), hero_photo_url=PHOTO)
    assert any("replaceAllText" in r for r in requests)
    assert sum("replaceAllShapesWithImage" in r for r in requests) == 1


def test_image_request_comes_last() -> None:
    requests = build_fill_requests(_listing(), hero_photo_url=PHOTO)
    assert "replaceAllShapesWithImage" in requests[-1]


def test_no_photo_leaves_the_tag_shape_alone() -> None:
    """That untouched frame is what `needs_photo` looks like on the flyer."""
    requests = build_fill_requests(_listing(), hero_photo_url=None)
    assert not any("replaceAllShapesWithImage" in r for r in requests)


def test_hero_photo_is_never_a_text_placeholder() -> None:
    """A text request for it would destroy the tag before the image lands."""
    requests = build_fill_requests(_listing(), hero_photo_url=PHOTO)
    texts = [r["replaceAllText"]["containsText"]["text"] for r in requests if "replaceAllText" in r]
    assert HERO_PHOTO_TAG not in texts


def test_unusable_photo_raises_rather_than_shipping_a_blank_frame() -> None:
    with pytest.raises(TemplateError):
        build_fill_requests(_listing(), hero_photo_url="https://example.com/photo.webp")


# --- filename ---------------------------------------------------------------


def test_filename_leads_with_the_address() -> None:
    assert flyer_filename(_listing()).startswith("123 Anywhere St")


def test_filename_strips_path_characters() -> None:
    name = flyer_filename(_listing(address="123 A/B St\nUnit 2"))
    assert "/" not in name
    assert "\n" not in name


def test_filename_is_bounded() -> None:
    assert len(flyer_filename(_listing(address="x" * 500))) <= 200


def test_filename_survives_missing_data() -> None:
    assert flyer_filename(_listing(address="", agent_name="")) == "unknown address — unknown agent"
