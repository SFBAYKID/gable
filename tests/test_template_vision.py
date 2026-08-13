"""Source-template vision is rendered, guarded, and fail closed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gable import spend
from gable.db.schema import apply_migrations, connect
from gable.pipeline.template_vision import inspect_source_template
from gable.pipeline.vision import Inspection


class FakeSlides:
    """Small discovery-resource stand-in for get and getThumbnail."""

    def __init__(self, *, pages: int = 1) -> None:
        """Set how many source pages a presentation reports."""
        self.page_count = pages
        self.operation = ""
        self.calls: list[str] = []

    def presentations(self) -> FakeSlides:
        """Match the Slides client chain."""
        return self

    def pages(self) -> FakeSlides:
        """Match the Slides pages resource chain."""
        return self

    def get(self, *, presentationId: str) -> FakeSlides:  # noqa: N803
        """Select the presentation read."""
        assert presentationId == "template-1"
        self.operation = "get"
        self.calls.append("get")
        return self

    def getThumbnail(self, **kwargs: Any) -> FakeSlides:  # noqa: ANN401, N802
        """Select and validate the large thumbnail request."""
        assert kwargs["presentationId"] == "template-1"
        assert kwargs["pageObjectId"] == "page-1"
        assert kwargs["thumbnailProperties_thumbnailSize"] == "LARGE"
        self.operation = "thumbnail"
        self.calls.append("thumbnail")
        return self

    def execute(self) -> dict[str, Any]:
        """Return the selected fake response."""
        if self.operation == "get":
            return {
                "slides": [{"objectId": f"page-{index + 1}"} for index in range(self.page_count)]
            }
        return {"contentUrl": "https://thumbnail.invalid/source.png"}


def test_no_model_key_fails_closed_without_rendering(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    slides = FakeSlides()

    result = inspect_source_template(
        connection,
        slides,
        "template-1",
        api_key="",
        model="gpt-5.6-sol",
    )

    assert result.checked is False
    assert slides.calls == []
    assert spend.total_spent(connection) == 0
    connection.close()


def test_one_page_is_rendered_and_inspected_behind_the_spend_guard(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    slides = FakeSlides()
    received: list[tuple[bytes, str, str]] = []

    def provider(image: bytes, api_key: str, model: str) -> Inspection:
        received.append((image, api_key, model))
        return Inspection(True, True)

    result = inspect_source_template(
        connection,
        slides,
        "template-1",
        api_key="test-key",
        model="gpt-5.6-sol",
        provider=provider,
        download=lambda url: b"png" if url.startswith("https://thumbnail.invalid/") else b"",
    )

    assert result.looks_right and result.confident and result.checked
    assert slides.calls == ["get", "thumbnail"]
    assert received == [(b"png", "test-key", "gpt-5.6-sol")]
    assert spend.total_spent(connection) == spend.VISION_RESERVE_USD
    connection.close()


def test_a_multi_page_source_never_reaches_the_paid_inspector(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    provider_called = False

    def provider(_image: bytes, _api_key: str, _model: str) -> Inspection:
        nonlocal provider_called
        provider_called = True
        return Inspection(True, True)

    result = inspect_source_template(
        connection,
        FakeSlides(pages=2),
        "template-1",
        api_key="test-key",
        model="gpt-5.6-sol",
        provider=provider,
    )

    assert result.checked is False
    assert provider_called is False
    assert spend.total_spent(connection) == 0
    connection.close()
