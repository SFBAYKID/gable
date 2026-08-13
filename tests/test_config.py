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
    ALLOWED_SLACK_CHANNEL_IDS,
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
    assert settings.photo_policy is PhotoPolicy.RETRIEVE_ONLY
    assert settings.poll_interval_seconds == 600
    assert settings.poll_busy_interval_seconds == 120
    assert settings.max_batch == 25
    assert settings.max_image_calls_per_listing == 1
    assert settings.photo_max_edge_px == 2400
    assert settings.photo_jpeg_quality == 85
    assert settings.photo_public_root == Path("/var/www/gable-photos")
    assert settings.photo_public_base == "http://143.110.146.87"
    assert settings.conversation_model == "gpt-5.6-sol"
    assert settings.vision_model == "gpt-5.6-sol"
    assert settings.image_model_hq == "gpt-image-2"
    assert settings.tab_responses == "Form Responses 1"
    assert settings.db_path == Path("/opt/gable/var/gable.db")


def test_slack_channel_defaults_to_the_production_channel() -> None:
    """A missing variable must not silently retarget Gable (CLAUDE.md 11)."""
    assert _load().slack_channel_id == DEFAULT_SLACK_CHANNEL_ID == "C0BP597644B"


@pytest.mark.parametrize("channel_id", ["C0BP597644B", "C0B02721MNK"])
def test_only_contract_slack_channels_are_accepted(channel_id: str) -> None:
    assert frozenset({"C0BP597644B", "C0B02721MNK"}) == ALLOWED_SLACK_CHANNEL_IDS
    settings = _load(GABLE_SLACK_CHANNEL_ID=channel_id)
    assert settings.slack_channel_id == channel_id


@pytest.mark.parametrize("channel_id", ["C1234567890", "general", "C0BP597644C"])
def test_unrelated_or_mistyped_slack_channel_is_rejected(channel_id: str) -> None:
    with pytest.raises(ConfigError, match="GABLE_SLACK_CHANNEL_ID"):
        _load(GABLE_SLACK_CHANNEL_ID=channel_id)


def test_settings_are_frozen() -> None:
    settings = _load()
    with pytest.raises(AttributeError):
        settings.max_batch = 999  # type: ignore[misc]


def test_whitespace_is_stripped() -> None:
    assert _load(GABLE_TAB_RESPONSES="  Form Responses 1  ").tab_responses == "Form Responses 1"


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
    assert "GABLE_SLACK_ALLOWED_USER_IDS" in joined
    assert "GABLE_DRIVE_ID" in joined
    assert "GABLE_DRIVE_TEMPLATES_FOLDER_ID" in joined
    assert "GABLE_DRIVE_OUTPUT_FOLDER_ID" in joined
    assert "OPENAI_IMAGE_API_KEY" in joined


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
            "GABLE_SLACK_ALLOWED_USER_IDS": "U12345678,U87654321",
            "FIRECRAWL_API_KEY": "fc-t",
            "GABLE_DRIVE_ID": "0Adrive",
            "GABLE_DRIVE_TEMPLATES_FOLDER_ID": "templates",
            "GABLE_DRIVE_OUTPUT_FOLDER_ID": "output",
            "OPENAI_IMAGE_API_KEY": "openai-t",
        },
        require_credentials=True,
    )
    assert settings.google_service_account_file == key


def test_only_two_stable_slack_user_ids_are_accepted() -> None:
    settings = _load(GABLE_SLACK_ALLOWED_USER_IDS=" U12345678, U87654321 ")
    assert settings.slack_allowed_user_ids == frozenset({"U12345678", "U87654321"})

    with pytest.raises(ConfigError, match="exactly two"):
        _load(GABLE_SLACK_ALLOWED_USER_IDS="U12345678")
    with pytest.raises(ConfigError, match="beginning with U or W"):
        _load(GABLE_SLACK_ALLOWED_USER_IDS="C12345678,U87654321")


def test_stale_oauth_settings_are_rejected_before_bolt_can_ignore_the_bot_token() -> None:
    with pytest.raises(ConfigError, match="SLACK_CLIENT_ID"):
        _load(SLACK_CLIENT_ID="legacy-client")
    with pytest.raises(ConfigError, match="SLACK_CLIENT_SECRET"):
        _load(SLACK_CLIENT_SECRET="legacy-secret")


# --- coercion ---------------------------------------------------------------


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on"])
def test_the_unimplemented_dry_run_switch_fails_closed(raw: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_DRY_RUN=raw)
    assert any("not a supported isolation mode" in problem for problem in excinfo.value.problems)


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off"])
def test_boolean_false_spellings(raw: str) -> None:
    assert _load(GABLE_DRY_RUN=raw).dry_run is False


def test_typo_in_boolean_is_rejected_not_treated_as_false() -> None:
    """`GABLE_DRY_RUN=ture` must not quietly start writing to a live Sheet."""
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_DRY_RUN="ture")
    assert any("not a boolean" in problem for problem in excinfo.value.problems)


def test_poll_interval_floor_is_enforced() -> None:
    """A 1-second poll would burn the Sheets quota and the droplet."""
    with pytest.raises(ConfigError):
        _load(GABLE_POLL_INTERVAL_SECONDS="1")


@pytest.mark.parametrize("root", ["relative", "/"])
def test_photo_public_root_must_be_a_specific_absolute_directory(root: str) -> None:
    with pytest.raises(ConfigError, match="GABLE_PHOTO_PUBLIC_ROOT"):
        _load(GABLE_PHOTO_PUBLIC_ROOT=root)


def test_photo_public_base_must_be_fetchable_by_slides() -> None:
    with pytest.raises(ConfigError, match="GABLE_PHOTO_PUBLIC_BASE"):
        _load(GABLE_PHOTO_PUBLIC_BASE="file:///var/www/gable-photos")


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


def test_image_call_allowance_cannot_exceed_one_per_listing() -> None:
    with pytest.raises(ConfigError, match="GABLE_MAX_IMAGE_CALLS_PER_LISTING"):
        _load(GABLE_MAX_IMAGE_CALLS_PER_LISTING="2")


def test_unknown_enum_lists_the_valid_options() -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_PHOTO_POLICY="generate_sometimes")
    problem = next(p for p in excinfo.value.problems if "GABLE_PHOTO_POLICY" in p)
    assert "retrieve_only" in problem
    assert "no_ai" in problem


# --- photo policy semantics -------------------------------------------------


@pytest.mark.parametrize(
    ("policy", "enhances"),
    [
        (PhotoPolicy.RETRIEVE_ONLY, True),
        (PhotoPolicy.GENERATE_WITH_APPROVAL, True),
        (PhotoPolicy.GENERATE_FREELY, True),
        (PhotoPolicy.NO_AI, False),
    ],
)
def test_policy_semantics(policy: PhotoPolicy, enhances: bool) -> None:
    """Only supplied-photo reprocessing is connected at runtime."""
    assert policy.allows_reprocessing is enhances


@pytest.mark.parametrize("policy", ["generate_with_approval", "generate_freely"])
def test_generation_policies_are_rejected_because_no_generator_is_connected(
    policy: str,
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_PHOTO_POLICY=policy)
    assert any("no synthetic-photo generator" in problem for problem in excinfo.value.problems)


def test_no_image_key_still_boots() -> None:
    """A deployment that never touches an image model is a normal state."""
    settings = _load()
    assert settings.images_available is False
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


def test_live_ai_key_is_read() -> None:
    settings = _load(OPENAI_IMAGE_API_KEY="sk-img")
    assert settings.openai_image_api_key == "sk-img"


# --- cross-field checks -----------------------------------------------------


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
    # These two names are deliberately rejected, not read. Documenting them as
    # usable settings would recreate the production OAuth/token conflict the
    # validator exists to prevent.
    read -= {"SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"}
    documented = set(re.findall(r"^([A-Z_]+)=", example, re.M))
    undocumented = sorted(read - documented)
    assert not undocumented, f".env.example is missing: {undocumented}"
