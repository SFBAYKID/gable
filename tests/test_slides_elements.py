"""Tests for reading text and images nested inside imported Slides groups."""

from __future__ import annotations

from typing import Any

from gable.slides.elements import descendants, presentation_elements, text_content


def test_nested_group_children_are_read_in_document_order() -> None:
    first: dict[str, Any] = {
        "objectId": "text-1",
        "shape": {"text": {"textElements": [{"textRun": {"content": "Price"}}]}},
    }
    second: dict[str, Any] = {"objectId": "image-1", "image": {}}
    grouped = [{"objectId": "group-1", "elementGroup": {"children": [first, second]}}]

    assert [element["objectId"] for element in descendants(grouped)] == ["text-1", "image-1"]
    assert text_content(first) == "Price"


def test_presentation_reader_preserves_the_parent_page_id() -> None:
    presentation = {
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    {
                        "objectId": "group-1",
                        "elementGroup": {"children": [{"objectId": "child-1", "image": {}}]},
                    }
                ],
            }
        ]
    }

    assert list(presentation_elements(presentation)) == [
        ("page-1", {"objectId": "child-1", "image": {}})
    ]
