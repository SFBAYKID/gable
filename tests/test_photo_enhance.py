"""Tests for the one paid real-photo upscaling path."""

from __future__ import annotations

import base64
import io
from collections.abc import Mapping

import httpx
import pytest
from PIL import Image

from gable.photos.enhance import (
    EnhancementError,
    EnhancementQualityError,
    composition_distance,
    upscale_real_photo,
)
from gable.photos.fit import image_dimensions


def _jpeg(
    width: int,
    height: int,
    colour: tuple[int, int, int] = (120, 160, 200),
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="JPEG", quality=95)
    return out.getvalue()


class FakePost:
    """Return one configured Image API response and record safe request fields."""

    def __init__(self, output: bytes, status_code: int = 200) -> None:
        """Configure output bytes and status without any network call."""
        self.output = output
        self.status_code = status_code
        self.data: dict[str, str] = {}
        self.files: Mapping[str, tuple[str, bytes, str]] = {}
        self.auth = ""

    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        data: Mapping[str, str],
        files: Mapping[str, tuple[str, bytes, str]],
        timeout: float,
    ) -> httpx.Response:
        """Capture the multipart contract and return a base64 result."""
        assert url == "https://api.openai.com/v1/images/edits"
        assert timeout == 180.0
        self.auth = headers["Authorization"]
        self.data = dict(data)
        self.files = files
        return httpx.Response(
            self.status_code,
            json={"data": [{"b64_json": base64.b64encode(self.output).decode("ascii")}]},
            request=httpx.Request("POST", url),
        )


def test_gpt_image_2_upscale_preserves_and_returns_exact_flyer_size() -> None:
    post = FakePost(_jpeg(1088, 1360))

    result = upscale_real_photo(
        _jpeg(200, 200),
        api_key="test-key",
        model="gpt-image-2",
        target_width=1080,
        target_height=1350,
        post=post,
    )

    assert image_dimensions(result) == (1080, 1350)
    assert post.auth == "Bearer test-key"
    assert post.data["size"] == "1088x1360"
    assert post.data["quality"] == "medium"
    assert "input_fidelity" not in post.data
    filename, uploaded, content_type = post.files["image[]"]
    assert filename == "property.jpg"
    assert content_type == "image/jpeg"
    assert image_dimensions(uploaded) == (1080, 1350)


def test_earlier_image_models_request_high_input_fidelity() -> None:
    post = FakePost(_jpeg(1024, 1536))

    upscale_real_photo(
        _jpeg(200, 200),
        api_key="test-key",
        model="gpt-image-1-mini",
        target_width=1080,
        target_height=1350,
        post=post,
    )

    assert post.data["size"] == "1024x1536"
    assert post.data["input_fidelity"] == "high"


def test_an_edit_that_changes_the_scene_is_rejected() -> None:
    post = FakePost(_jpeg(1088, 1360, (255, 255, 255)))

    with pytest.raises(EnhancementQualityError, match="changed the property"):
        upscale_real_photo(
            _jpeg(200, 200, (0, 0, 0)),
            api_key="test-key",
            model="gpt-image-2",
            target_width=1080,
            target_height=1350,
            post=post,
        )


def test_a_vendor_error_never_exposes_its_response_body() -> None:
    post = FakePost(b"ignored", status_code=500)

    with pytest.raises(EnhancementError) as excinfo:
        upscale_real_photo(
            _jpeg(200, 200),
            api_key="test-key",
            model="gpt-image-2",
            target_width=1080,
            target_height=1350,
            post=post,
        )

    assert "500" not in str(excinfo.value)
    assert "test-key" not in str(excinfo.value)


def test_a_transport_error_is_translated_without_the_request() -> None:
    def fail(
        url: str,
        _headers: Mapping[str, str],
        _data: Mapping[str, str],
        _files: Mapping[str, tuple[str, bytes, str]],
        _timeout: float,
    ) -> httpx.Response:
        raise httpx.ConnectError("test transport details", request=httpx.Request("POST", url))

    with pytest.raises(EnhancementError, match="could not be reached") as excinfo:
        upscale_real_photo(
            _jpeg(200, 200),
            api_key="test-key",
            model="gpt-image-2",
            target_width=1080,
            target_height=1350,
            post=fail,
        )

    assert "transport details" not in str(excinfo.value)
    assert "test-key" not in str(excinfo.value)


def test_composition_distance_ignores_resolution_but_not_a_replacement() -> None:
    low = _jpeg(200, 250, (30, 60, 90))
    high = _jpeg(1080, 1350, (30, 60, 90))
    replacement = _jpeg(1080, 1350, (240, 240, 240))

    assert composition_distance(low, high) < 0.01
    assert composition_distance(low, replacement) > 0.5
