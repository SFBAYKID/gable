"""Tests for environment parsing.

Bias is toward the failure paths. A misconfigured droplet that boots anyway and
misbehaves quietly is far worse than one that refuses to start with a list of
what is wrong, so most of these assert that bad input is *rejected*.

`Settings.load` is always called with an explicit `environ` — never the real
one — so these stay hermetic and cannot be perturbed by a developer's shell.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from gable.config import (
    DEFAULT_SLACK_CHANNEL_ID,
    ConfigError,
    PhotoPolicy,
    Settings,
)


def _minimal(**overrides: str) -> dict[str, str]:
    """The smallest environment that parses cleanly with credentials waived."""
    env = {"GABLE_SHEET_ID": "sheet-abc123"}
    env.update(overrides)
    return env


def _load(**overrides: str) -> Settings:
    return Settings.load(_minimal(**overrides), require_credentials=False)


# --- defaults ---------------------------------------------------------------


def test_defaults_match_dotenv_example() -> None:
    settings = _load()
    assert settings.photo_policy is PhotoPolicy.GENERATE_WITH_APPROVAL
    assert settings.photo_min_confidence == 0.75
    assert settings.poll_interval_seconds == 600
    assert settings.poll_busy_interval_seconds == 120
    assert settings.max_batch == 25
    assert settings.max_description_chars == 400
    assert settings.max_retries == 3
    assert settings.max_image_calls_per_listing == 1
    assert settings.photo_max_edge_px == 2400
    assert settings.photo_jpeg_quality == 85
    assert settings.tab_responses == "Form Responses 1"
    assert settings.tab_agents == "Sales_People"
    assert settings.db_path == Path("/opt/gable/var/gable.db")


def test_slack_channel_defaults_to_the_only_permitted_channel() -> None:
    """A missing variable must not silently retarget Gable (CLAUDE.md 11)."""
    assert _load().slack_channel_id == DEFAULT_SLACK_CHANNEL_ID == "C0BP597644B"


def test_settings_are_frozen() -> None:
    settings = _load()
    with pytest.raises(AttributeError):
        settings.max_batch = 999  # type: ignore[misc]


def test_whitespace_is_stripped() -> None:
    assert _load(GABLE_TAB_RUNS="  Runs  ").tab_runs == "Runs"


# --- required values --------------------------------------------------------


def test_missing_sheet_id_is_reported() -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.load({}, require_credentials=False)
    assert any("GABLE_SHEET_ID" in problem for problem in excinfo.value.problems)


def test_all_problems_are_collected_not_just_the_first() -> None:
    """One boot must reveal the whole list, not force six deploy cycles."""
    with pytest.raises(ConfigError) as excinfo:
        Settings.load(
            {
                "GABLE_MAX_BATCH": "not-a-number",
                "GABLE_DRY_RUN": "ture",
                "GABLE_PHOTO_POLICY": "whatever",
                "LOG_LEVEL": "LOUD",
            },
            require_credentials=False,
        )
    problems = excinfo.value.problems
    assert len(problems) >= 5
    joined = " ".join(problems)
    for name in ("GABLE_SHEET_ID", "GABLE_MAX_BATCH", "GABLE_DRY_RUN", "GABLE_PHOTO_POLICY"):
        assert name in joined


def test_missing_credentials_are_reported_when_required() -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.load({"GABLE_SHEET_ID": "x"}, require_credentials=True)
    joined = " ".join(excinfo.value.problems)
    assert "SLACK_BOT_TOKEN" in joined
    assert "FIRECRAWL_API_KEY" in joined


def test_credential_value_never_appears_in_an_error_message() -> None:
    """A truncated token in an error string is still a leaked token."""
    secret = "xoxb-do-not-echo-me-anywhere-1234567890"
    with pytest.raises(ConfigError) as excinfo:
        Settings.load(
            {"SLACK_BOT_TOKEN": secret, "GABLE_MAX_BATCH": "0"},
            require_credentials=False,
        )
    assert secret not in str(excinfo.value)


def test_service_account_path_must_exist_when_credentials_required(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.load(
            {"GABLE_SHEET_ID": "x", "GOOGLE_SERVICE_ACCOUNT_FILE": str(tmp_path / "nope.json")},
            require_credentials=True,
        )
    assert any("no file at" in problem for problem in excinfo.value.problems)


def test_service_account_path_accepted_when_it_exists(tmp_path: Path) -> None:
    key = tmp_path / "sa.json"
    key.write_text("{}", encoding="utf-8")
    settings = Settings.load(
        {
            "GABLE_SHEET_ID": "x",
            "GOOGLE_SERVICE_ACCOUNT_FILE": str(key),
            "SLACK_BOT_TOKEN": "xoxb-t",
            "SLACK_APP_TOKEN": "xapp-t",
            "FIRECRAWL_API_KEY": "fc-t",
        },
        require_credentials=True,
    )
    assert settings.google_service_account_file == key


# --- coercion ---------------------------------------------------------------


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on"])
def test_boolean_true_spellings(raw: str) -> None:
    assert _load(GABLE_DRY_RUN=raw).dry_run is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off"])
def test_boolean_false_spellings(raw: str) -> None:
    assert _load(GABLE_DRY_RUN=raw).dry_run is False


def test_typo_in_boolean_is_rejected_not_treated_as_false() -> None:
    """`GABLE_DRY_RUN=ture` must not quietly start writing to a live Sheet."""
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_DRY_RUN="ture")
    assert any("not a boolean" in problem for problem in excinfo.value.problems)


def test_out_of_range_values_are_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_PHOTO_MIN_CONFIDENCE="1.5")
    assert any("above the maximum" in problem for problem in excinfo.value.problems)


def test_poll_interval_floor_is_enforced() -> None:
    """A 1-second poll would burn the Sheets quota and the droplet."""
    with pytest.raises(ConfigError):
        _load(GABLE_POLL_INTERVAL_SECONDS="1")


def test_busy_poll_interval_floor_is_enforced() -> None:
    """The busy rate gets the same floor as the quiet one."""
    with pytest.raises(ConfigError):
        _load(GABLE_POLL_BUSY_INTERVAL_SECONDS="1")


def test_poll_schedule_maps_the_quiet_variable_to_the_quiet_rate() -> None:
    """`GABLE_POLL_INTERVAL_SECONDS` is nights and weekends, not the busy rate.

    Getting these backwards would poll every 10 minutes during the working day
    and every 2 minutes at 3am — the exact opposite of the intent, and silent.
    """
    settings = _load(
        GABLE_POLL_INTERVAL_SECONDS="900",
        GABLE_POLL_BUSY_INTERVAL_SECONDS="60",
    )
    schedule = settings.poll_schedule
    assert schedule.quiet_interval_seconds == 900
    assert schedule.busy_interval_seconds == 60
    # Monday 09:00 Central — inside the window.
    assert schedule.interval_seconds(datetime(2026, 8, 10, 14, 0, tzinfo=UTC)) == 60
    # Monday 04:00 Central — before it opens. The window now runs to 19:00
    # Pacific and covers weekends, so a quiet hour has to be an early one.
    assert schedule.interval_seconds(datetime(2026, 8, 10, 9, 0, tzinfo=UTC)) == 900


def test_batch_ceiling_is_enforced() -> None:
    with pytest.raises(ConfigError):
        _load(GABLE_MAX_BATCH="5000")


def test_unknown_enum_lists_the_valid_options() -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_PHOTO_POLICY="generate_sometimes")
    problem = next(p for p in excinfo.value.problems if "GABLE_PHOTO_POLICY" in p)
    assert "retrieve_only" in problem
    assert "no_ai" in problem


# --- photo policy semantics -------------------------------------------------


@pytest.mark.parametrize(
    ("policy", "generates", "enhances", "needs_approval"),
    [
        (PhotoPolicy.RETRIEVE_ONLY, False, True, False),
        (PhotoPolicy.GENERATE_WITH_APPROVAL, True, True, True),
        (PhotoPolicy.GENERATE_FREELY, True, True, False),
        (PhotoPolicy.NO_AI, False, False, False),
    ],
)
def test_policy_semantics(
    policy: PhotoPolicy, generates: bool, enhances: bool, needs_approval: bool
) -> None:
    """All four policies are implemented faithfully (CLAUDE.md section 8)."""
    assert policy.allows_generation is generates
    assert policy.allows_reprocessing is enhances
    assert policy.requires_approval_before_generating is needs_approval


def test_generate_freely_without_an_image_key_is_rejected() -> None:
    """The one unsatisfiable combination: auto-generate, nothing to generate with."""
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_PHOTO_POLICY="generate_freely")
    assert any("OPENAI_IMAGE_API_KEY" in problem for problem in excinfo.value.problems)


def test_generate_freely_with_an_image_key_is_accepted() -> None:
    settings = _load(GABLE_PHOTO_POLICY="generate_freely", OPENAI_IMAGE_API_KEY="sk-abc123def456")
    assert settings.generation_available is True


def test_no_image_key_still_boots() -> None:
    """A deployment that never touches an image model is a normal state."""
    settings = _load()
    assert settings.images_available is False
    assert settings.generation_available is False
    assert settings.reprocessing_enabled is False


def test_reprocessing_needs_both_the_flag_and_a_key() -> None:
    """Reshaping a real photo to fit the frame still calls an image model."""
    assert _load(GABLE_PHOTO_REPROCESS="true").reprocessing_enabled is False
    assert (
        _load(
            GABLE_PHOTO_REPROCESS="true", OPENAI_IMAGE_API_KEY="sk-abc123def456"
        ).reprocessing_enabled
        is True
    )


def test_no_ai_policy_overrides_the_reprocess_flag() -> None:
    """Policy is authoritative; the flag is subordinate."""
    settings = _load(
        GABLE_PHOTO_POLICY="no_ai",
        GABLE_PHOTO_REPROCESS="true",
        OPENAI_IMAGE_API_KEY="sk-abc123def456",
    )
    assert settings.photo_reprocess is True
    assert settings.reprocessing_enabled is False
    assert settings.generation_available is False


def test_ai_keys_are_read() -> None:
    settings = _load(OPENAI_IMAGE_API_KEY="sk-img", ANTHROPIC_API_KEY="sk-ant")
    assert settings.openai_image_api_key == "sk-img"
    assert settings.anthropic_api_key == "sk-ant"


# --- cross-field checks -----------------------------------------------------


def test_non_https_spaces_base_is_rejected() -> None:
    """Google Slides fetches inserted images over the public internet, via HTTPS."""
    with pytest.raises(ConfigError) as excinfo:
        _load(SPACES_PUBLIC_BASE="http://gable-photos.nyc3.digitaloceanspaces.com")
    assert any("https" in problem for problem in excinfo.value.problems)


def test_disabling_redaction_is_refused() -> None:
    """Redaction is a mechanism, not a preference (CLAUDE.md section 3)."""
    with pytest.raises(ConfigError) as excinfo:
        _load(LOG_REDACT_SECRETS="false")
    assert any("must never be logged" in problem for problem in excinfo.value.problems)


def test_bad_log_level_is_rejected() -> None:
    with pytest.raises(ConfigError):
        _load(LOG_LEVEL="chatty")


def test_env_example_documents_every_variable_the_code_reads() -> None:
    """CLAUDE.md section 10 requires this, and it had already drifted.

    config kept reading GABLE_DRIVE_PHOTOS_FOLDER_ID after the Photos folder was
    deleted and the variable was dropped from .env.example, so the documented
    configuration and the real one disagreed with nothing to catch it.
    """
    import re

    root = Path(__file__).resolve().parent.parent
    config_source = (root / "src" / "gable" / "config.py").read_text(encoding="utf-8")
    example = (root / ".env.example").read_text(encoding="utf-8")

    read = set(
        re.findall(
            r'"((?:GABLE|SLACK|OPENAI|ANTHROPIC|SPACES|GOOGLE|LOG|FIRECRAWL)_[A-Z_]+)"',
            config_source,
        )
    )
    documented = set(re.findall(r"^([A-Z_]+)=", example, re.M))
    undocumented = sorted(read - documented)
    assert not undocumented, f".env.example is missing: {undocumented}"
