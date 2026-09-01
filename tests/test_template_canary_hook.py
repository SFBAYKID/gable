"""An added or edited design is built once, and what the build showed is posted."""

from __future__ import annotations

from pathlib import Path

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.template_triage import TemplateTriage
from gable.pipeline.vision import Inspection
from gable.slides.library import TemplateFile
from tests.test_template_triage import _presentation, _say_into


def test_an_edited_design_is_built_once_and_what_it_showed_is_posted(tmp_path: Path) -> None:
    """The scan measured Under Contract clean after Carmen's edit; a build would have said more."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("uc", "Under Contract", "rev-1")]
    said: list[str] = []
    built: list[str] = []

    def dry_build(item: TemplateFile) -> str:
        built.append(item.file_id)
        return (
            "I also built a test flyer. The agent photo runs about 20 points past the bottom edge."
        )

    triage = TemplateTriage(
        connection,
        lambda: files,
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(True, True),
        dry_build=dry_build,
    )
    assert triage.scan_new() == 0, "the first read adopts silently"
    assert built == []

    files[0] = TemplateFile("uc", "Under Contract", "rev-2")
    assert triage.scan_new() == 1

    assert built == ["uc"], "an edited design is built exactly once"
    assert len(said) == 1
    assert "past the bottom edge" in said[0]
    audit = store.template_audit(connection, "uc")
    assert audit is not None and audit.status == "ready"
    connection.close()


def test_a_clean_test_build_keeps_an_edited_design_quiet(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("uc", "Under Contract", "rev-1")]
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: files,
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(True, True),
        dry_build=lambda _item: "",
    )
    triage.scan_new()
    files[0] = TemplateFile("uc", "Under Contract", "rev-2")
    triage.scan_new()

    assert said == [], "a clean re-read and a clean build are not news"
    connection.close()


def test_a_structurally_refused_design_is_not_built(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("bad", "Sold", "rev-1")]
    said: list[str] = []
    built: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: files,
        # No hero frame and no fields: a structural refusal.
        lambda _file_id: {
            "pageSize": {"width": {"magnitude": 10_000_000}, "height": {"magnitude": 12_500_000}},
            "slides": [{"objectId": "page-1", "pageElements": []}],
        },
        _say_into(said),
        dry_build=lambda item: built.append(item.file_id) or "never",
    )
    triage.scan_new()
    files[0] = TemplateFile("bad", "Sold", "rev-2")
    triage.scan_new()

    assert built == []
    connection.close()
