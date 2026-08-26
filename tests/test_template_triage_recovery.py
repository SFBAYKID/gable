"""Answering a person who is actively fixing several designs at once.

These are the 2026-08-26 regressions: Carmen replaced six templates in twenty
minutes, and Gable repeated a refusal she was already acting on, sent her to
convert a file it could never have picked, told her to restore one she had
correctly deleted, and had nowhere for "check them all again" to land.

Assumes: nothing touches Drive or Slack. Every template list and presentation
is supplied by the test.
"""

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

PPTX: str = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _say_into(messages: list[str]) -> Callable[[str, str | None], str]:
    """Return a typed Slack recorder with a stable root per posted message."""

    def say(text: str, _thread: str | None) -> str:
        messages.append(text)
        return f"thread-{len(messages)}"

    return say


def _text(object_id: str, text: str, width_pt: float) -> dict[str, Any]:
    """One filled text box, sized in points the way the real sources are."""
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


def _presentation() -> dict[str, Any]:
    """A structurally clean source design with one photo well."""
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
                    _text("name", "AGENT NAME", 900),
                    _text("email", "Email", 700),
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


def test_the_same_fault_is_not_repeated_when_a_refused_file_is_edited(tmp_path: Path) -> None:
    """Carmen was told the same thing twice, four minutes apart, on 2026-08-26.

    She uploaded a .pptx, was told it was not a Google Slides design, answered
    "I am working on it now", touched the file, and was told the identical
    sentence again because the Drive revision had changed.
    """
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
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("pptx-1", "Brittany Tawney Static.pptx", "rev-1", PPTX))

    assert triage.scan_new() == 1
    assert len(said) == 1

    # She saves the file again; the fault is unchanged, so there is nothing new.
    files[0] = TemplateFile("pptx-1", "Brittany Tawney Static.pptx", "rev-2", PPTX)
    assert triage.scan_new() == 1
    assert len(said) == 1, said

    # The revision is still recorded, so listing clearance stays truthful.
    audit = store.template_audit(connection, "pptx-1")
    assert audit is not None
    assert audit.modified_time == "rev-2"
    assert audit.blocker_kind == store.BLOCKER_UNSUPPORTED
    connection.close()


def test_a_file_named_outside_the_form_is_told_it_will_never_be_picked(tmp_path: Path) -> None:
    """Converting `Brittany Tawney Static.pptx` would still never be selected."""
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
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("pptx-1", "Brittany Tawney Static.pptx", "one", PPTX))

    assert triage.scan_new() == 1
    assert "Brittany Tawney Static" in said[0]
    assert "never pick" in said[0]
    assert not violations(said[0])

    # A correctly named upload is told only that the format is wrong.
    said.clear()
    files.append(TemplateFile("pptx-2", "Open House.pptx", "one", PPTX))
    assert triage.scan_new() == 1
    assert "never pick" not in said[0], said[0]
    connection.close()


def test_removing_a_file_gable_asked_to_replace_is_not_treated_as_a_mistake(
    tmp_path: Path,
) -> None:
    """Carmen deleted a converted .pptx and was told to put it back."""
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
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("pptx-1", "Open House.pptx", "one", PPTX))
    assert triage.scan_new() == 1

    files.clear()
    answer = triage.recheck_file("pptx-1")
    assert "Put it back" not in answer, answer
    assert "what I asked for" in answer
    assert not violations(answer)
    audit = store.template_audit(connection, "pptx-1")
    assert audit is not None
    assert audit.status == "retired"
    connection.close()


def test_a_whole_folder_recheck_answers_without_a_matching_thread(tmp_path: Path) -> None:
    """Asking "I just imported new templates, can you check again?" had nowhere to land."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [
        TemplateFile("ok-1", "Sold", "one"),
        TemplateFile("ok-2", "Open House", "one"),
    ]
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(True, True),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])

    answer = triage.recheck_catalog()
    assert "Sold" in answer and "Open House" in answer
    assert "ready to build from" in answer
    assert not violations(answer)
    # The sweep speaks once, in its return value, rather than per design.
    assert said == []
    connection.close()


def test_a_design_refused_only_on_looks_is_still_reported_as_buildable(tmp_path: Path) -> None:
    """The open-house tag overhang is deliberate; it must not read as broken."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("look-1", "Open House", "one")]
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into([]),
        look_at=lambda _file_id: Inspection(
            False, True, problems=["The tag is cut off at the right edge"]
        ),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])

    answer = triage.recheck_catalog()
    assert "ready to build from" in answer
    assert "Open House" in answer
    audit = store.template_audit(connection, "look-1")
    assert audit is not None
    assert audit.blocker_kind == store.BLOCKER_VISUAL
    connection.close()


def test_a_dead_thread_still_answers_the_question_that_was_asked(tmp_path: Path) -> None:
    """Carmen asked "can you check again?" in the thread of a file she had deleted.

    She was told only that the dead .pptx could not be found, and nothing about
    the six designs she had just finished importing.
    """
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(True, True),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("pptx-1", "Open House.pptx", "one", PPTX))
    assert triage.scan_new() == 1
    thread = store.template_audit(connection, "pptx-1")
    assert thread is not None

    # She converts it and removes the .pptx, leaving real designs behind.
    files.clear()
    files.extend([TemplateFile("ok-1", "Open House", "one"), TemplateFile("ok-2", "Sold", "one")])

    answer = triage.recheck(thread.slack_thread_ts)
    assert "what I asked for" in answer
    assert "ready to build from" in answer
    assert "Open House" in answer and "Sold" in answer
    assert not violations(answer)
    connection.close()


def test_a_thread_gable_does_not_own_gets_the_folder_rather_than_a_shrug(
    tmp_path: Path,
) -> None:
    """Saying "I could not match this thread to a template" was never useful."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("ok-1", "Sold", "one")]
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into([]),
        look_at=lambda _file_id: Inspection(True, True),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])

    answer = triage.recheck("9999.0000")
    assert "could not match this thread" not in answer
    assert "ready to build from" in answer
    connection.close()
