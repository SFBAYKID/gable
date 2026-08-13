"""New source templates are measured once and rechecked from their owned thread."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.template_triage import TemplateTriage
from gable.pipeline.vision import Inspection
from gable.slides import fitting
from gable.slides.library import TemplateFile
from gable.voice import violations


def _say_into(messages: list[str]) -> Callable[[str, str | None], str]:
    """Return a typed Slack recorder with a stable root per posted message."""

    def say(text: str, _thread: str | None) -> str:
        messages.append(text)
        return f"thread-{len(messages)}"

    return say


def _text(object_id: str, text: str, width_pt: float) -> dict[str, Any]:
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
                            "content": text,
                            "style": {"fontSize": {"magnitude": 20, "unit": "PT"}},
                        }
                    }
                ]
            },
        },
    }


def _presentation(email_width: float = 700) -> dict[str, Any]:
    return {
        "pageSize": {
            "width": {"magnitude": 10_000_000},
            "height": {"magnitude": 12_500_000},
        },
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    _text("address", "[PROPERTY ADDRESS]", 900),
                    _text("email", "Email", email_width),
                    {
                        "objectId": "hero",
                        "size": {
                            "width": {"magnitude": 8_000_000},
                            "height": {"magnitude": 6_000_000},
                        },
                        "transform": {"scaleX": 1, "scaleY": 1},
                        "shape": {"shapeProperties": {"shapeBackgroundFill": {}}},
                    },
                ],
            }
        ],
    }


def test_first_scan_adopts_existing_files_without_flooding_slack(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("old-1", "New Listing", "one")]
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: files,
        lambda _file_id: _presentation(),
        _say_into(said),
    )

    assert triage.scan_new() == 0
    assert said == []
    assert store.template_catalog_adopted(connection)
    assert store.template_audit(connection, "old-1") is not None
    assert triage.scan_new() == 0
    assert said == []
    connection.close()


def test_new_file_is_measured_and_owns_a_recheck_thread(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    presentations: dict[str, dict[str, Any]] = {}
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda file_id: presentations[file_id],
        _say_into(said),
    )
    assert triage.scan_new() == 0

    files.append(TemplateFile("new-1", "New Listing", "one"))
    presentations["new-1"] = _presentation(email_width=100)
    assert triage.scan_new() == 1
    assert len(said) == 1
    assert "agent email" in said[0]
    assert not violations(said[0])
    audit = store.template_for_thread(connection, "thread-1")
    assert audit is not None and audit.status == "needs_template"

    presentations["new-1"] = _presentation(email_width=700)
    files[0] = TemplateFile("new-1", "New Listing", "two")
    outcome = triage.recheck("thread-1")
    assert "did not find a structural, text-capacity, or visible layout problem" in outcome
    refreshed = store.template_for_thread(connection, "thread-1")
    assert refreshed is not None and refreshed.modified_time == "two"
    assert refreshed.status == "ready"
    connection.close()


def test_duplicate_new_template_names_are_rejected_without_guessing(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
    )
    triage.scan_new()
    files.extend(
        [
            TemplateFile("one", "New Listing", "one"),
            TemplateFile("two", " New   Listing ", "one"),
        ]
    )

    assert triage.scan_new() == 2
    assert all("another file has the same name" in message for message in said)

    still_duplicate = triage.recheck("thread-1")
    assert "another file has the same name" in still_duplicate
    duplicate_audit = store.template_for_thread(connection, "thread-1")
    assert duplicate_audit is not None and duplicate_audit.status == "needs_template"

    files.pop()
    resolved = triage.recheck("thread-1")
    assert "did not find a structural, text-capacity, or visible layout problem" in resolved
    resolved_audit = store.template_for_thread(connection, "thread-1")
    assert resolved_audit is not None and resolved_audit.status == "ready"
    connection.close()


def test_a_new_powerpoint_is_named_as_unsupported_instead_of_ignored(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
    )
    triage.scan_new()
    files.append(
        TemplateFile(
            "pptx-1",
            "New Listing.pptx",
            "one",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    )

    assert triage.scan_new() == 1
    assert "not a Google Slides design" in said[0]
    assert not violations(said[0])
    connection.close()


def test_visual_uncertainty_blocks_template_certification(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(False, False, checked=False),
    )
    triage.scan_new()
    files.append(TemplateFile("new-1", "New Listing", "one"))

    assert triage.scan_new() == 1
    assert "could not complete its visual inspection" in said[0]
    audit = store.template_audit(connection, "new-1")
    assert audit is not None and audit.status == "needs_template"
    connection.close()


def test_recheck_names_the_visual_stage_and_reports_a_visible_defect(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(
            False,
            True,
            ["The contact details overlap the divider line."],
        ),
    )
    triage.scan_new()
    files.append(TemplateFile("new-1", "New Listing", "one"))
    triage.scan_new()
    stages: list[str] = []

    outcome = triage.recheck("thread-1", stages.append)

    assert stages == ["is inspecting the updated template..."]
    assert "contact details overlap the divider line" in outcome
    assert not violations(outcome)
    connection.close()


def test_a_failed_slack_post_retries_the_stored_verdict_without_reinspection(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    visual_calls = 0
    posts = 0

    def look(_file_id: str) -> Inspection:
        nonlocal visual_calls
        visual_calls += 1
        return Inspection(True, True)

    def say(_text: str, _thread: str | None) -> str:
        nonlocal posts
        posts += 1
        return "" if posts == 1 else "thread-retry"

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        say,
        look_at=look,
    )
    triage.scan_new()
    files.append(TemplateFile("new-1", "New Listing", "one"))

    assert triage.scan_new() == 1
    first = store.template_audit(connection, "new-1")
    assert first is not None and first.slack_thread_ts == ""
    assert triage.scan_new() == 0
    retried = store.template_audit(connection, "new-1")
    assert retried is not None and retried.slack_thread_ts == "thread-retry"
    assert visual_calls == 1
    assert posts == 2
    connection.close()
