"""Tests for the Slack-free local entry point."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from gable.cli import _local_requirements
from gable.config import Settings


def _settings(tmp_path: Path) -> Settings:
    key = tmp_path / "service-account.json"
    key.write_text("{}", encoding="utf-8")
    return Settings.load(
        {
            "GOOGLE_SERVICE_ACCOUNT_FILE": str(key),
            "GABLE_SHEET_ID": "sheet",
            "GABLE_DRIVE_ID": "0Adrive",
            "GABLE_DRIVE_OUTPUT_FOLDER_ID": "outputs",
        },
        use_dotenv=False,
        require_credentials=False,
    )


def test_local_entrypoint_does_not_require_slack_credentials(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert settings.slack_bot_token == ""
    assert settings.slack_app_token == ""
    assert _local_requirements(settings) == []


def test_local_entrypoint_names_missing_google_configuration(tmp_path: Path) -> None:
    settings = replace(
        _settings(tmp_path),
        google_service_account_file=tmp_path / "missing.json",
        drive_id="",
        drive_output_folder_id="",
    )

    assert _local_requirements(settings) == [
        "GOOGLE_SERVICE_ACCOUNT_FILE must name an existing key file",
        "GABLE_DRIVE_ID is required",
        "GABLE_DRIVE_OUTPUT_FOLDER_ID is required",
    ]
