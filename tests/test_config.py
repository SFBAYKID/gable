"""Tests for environment parsing.

Bias is toward the failure paths. A misconfigured droplet that boots anyway and
misbehaves quietly is far worse than one that refuses to start with a list of
what is wrong, so most of these assert that bad input is *rejected*.

`Settings.load` is always called with an explicit `environ` — never the real
one — so these stay hermetic and cannot be perturbed by a developer's shell.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gable.config import (
    DEFAULT_SLACK_CHANNEL_ID,
    ConfigError,
    ImageProvider,
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
    assert settings.poll_interval_seconds == 180
    assert settings.max_batch == 25
    assert settings.max_description_chars == 400
    assert settings.max_retries == 3
    assert settings.max_image_calls_per_listing == 1
    assert settings.photo_max_edge_px == 2400
    assert settings.photo_jpeg_quality == 85
    assert settings.tab_responses == "Form Responses 1"


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
    assert policy.allows_enhancement is enhances
    assert policy.requires_approval_before_generating is needs_approval


def test_generate_freely_without_a_usable_provider_is_rejected() -> None:
    """The one unsatisfiable combination: automatic generation, nothing to generate with."""
    with pytest.raises(ConfigError) as excinfo:
        _load(GABLE_PHOTO_POLICY="generate_freely", GABLE_IMAGE_PROVIDER="none")
    assert any("usable image provider" in problem for problem in excinfo.value.problems)


def test_generate_freely_with_a_provider_but_no_key_is_rejected() -> None:
    """A selected provider with a blank key is not a usable provider."""
    with pytest.raises(ConfigError):
        _load(GABLE_PHOTO_POLICY="generate_freely", GABLE_IMAGE_PROVIDER="openai")


def test_generate_freely_with_a_usable_provider_is_accepted() -> None:
    settings = _load(
        GABLE_PHOTO_POLICY="generate_freely",
        GABLE_IMAGE_PROVIDER="openai",
        OPENAI_API_KEY="sk-abc123",
    )
    assert settings.generation_available is True


def test_dotenv_example_defaults_boot_cleanly() -> None:
    """The shipped defaults must start: provider selected, key blank, approval policy.

    This is the regression that motivated the degrade-don't-fail rule. A Phase 1
    deployment generates no images at all and must not be blocked by that.
    """
    settings = _load(
        GABLE_PHOTO_POLICY="generate_with_approval",
        GABLE_IMAGE_PROVIDER="openai",
        OPENAI_API_KEY="",
        GABLE_PHOTO_ENHANCE="true",
    )
    assert settings.provider_is_usable is False
    assert settings.generation_available is False
    assert settings.enhancement_enabled is True


def test_no_ai_policy_overrides_the_enhance_flag() -> None:
    """Policy is authoritative; the flag is subordinate (CLAUDE.md section 8)."""
    settings = _load(GABLE_PHOTO_POLICY="no_ai", GABLE_PHOTO_ENHANCE="true")
    assert settings.photo_enhance is True
    assert settings.enhancement_enabled is False
    assert settings.generation_available is False


def test_provider_without_its_key_is_not_usable_but_still_boots() -> None:
    settings = _load(GABLE_IMAGE_PROVIDER="openai", GABLE_PHOTO_POLICY="retrieve_only")
    assert settings.image_provider is ImageProvider.OPENAI
    assert settings.provider_is_usable is False


def test_provider_with_its_key_is_usable() -> None:
    settings = _load(
        GABLE_IMAGE_PROVIDER="gemini",
        GEMINI_API_KEY="gem-abcdefgh",
        GABLE_PHOTO_POLICY="retrieve_only",
    )
    assert settings.image_provider is ImageProvider.GEMINI
    assert settings.provider_is_usable is True
    # retrieve_only never generates, no matter how usable the provider is.
    assert settings.generation_available is False


# --- cross-field checks -----------------------------------------------------


def test_non_https_spaces_base_is_rejected() -> None:
    """Canva requires an external HTTPS URL for image cells (CLAUDE.md 4.2)."""
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
