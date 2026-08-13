"""Tests for the Drive and Slides settings added with the Google Slides pivot.

The shared-drive check is the one that matters most: a My Drive folder id parses
perfectly well and then fails on the first render with `StorageQuotaExceeded`,
because service accounts have no storage quota of their own. Catching it at
startup turns a confusing 403 into a sentence.
"""

from __future__ import annotations

import pytest

from gable.config import ConfigError, Settings

SHARED_DRIVE_ID = "0AE0VE3GY-xguUk9PVA"
TEMPLATES_FOLDER_ID = "1lW6pB8ZNEMZlcCVOIB8TtyzZkV4WFBdI"


def _load(**overrides: str) -> Settings:
    env = {"GABLE_SHEET_ID": "sheet-abc123"}
    env.update(overrides)
    return Settings.load(env, require_credentials=False)


def test_drive_defaults_are_empty_not_invented() -> None:
    settings = _load()
    assert settings.drive_id == ""
    assert settings.drive_templates_folder_id == ""
    assert settings.drive_output_folder_id == ""


def test_real_ids_parse() -> None:
    settings = _load(
        GABLE_DRIVE_ID=SHARED_DRIVE_ID,
        GABLE_DRIVE_TEMPLATES_FOLDER_ID=TEMPLATES_FOLDER_ID,
    )
    assert settings.drive_id == SHARED_DRIVE_ID
    assert settings.drive_templates_folder_id == TEMPLATES_FOLDER_ID


def test_a_my_drive_folder_id_is_rejected() -> None:
    """The failure this check exists to prevent is a 403 on the first render."""
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_DRIVE_ID=TEMPLATES_FOLDER_ID)
    assert any("shared drive" in problem for problem in excinfo.value.problems)


def test_blank_drive_id_is_allowed() -> None:
    """Not configured yet is a normal state; wrong is not."""
    assert _load(GABLE_DRIVE_ID="").drive_id == ""


def test_slide_size_defaults_match_the_template_library() -> None:
    """Corner House templates are Instagram Post 4:5, verified 1080x1350 on export."""
    settings = _load()
    assert (settings.slide_width_px, settings.slide_height_px) == (1080, 1350)
