"""Unambiguous template-triage replies never depend on model interpretation."""

from __future__ import annotations

import pytest

from gable.slackapp.brain import think


@pytest.mark.parametrize(
    "message",
    [
        "yes, run again",
        "run again",
        "I updated the template",
        "check the template again",
        "check the updated template",
    ],
)
def test_run_again_reloads_the_updated_drive_source_without_a_model_key(
    message: str,
) -> None:
    decision = think(message, api_key="")

    assert decision.tool == "rebuild_flyer"
    assert decision.arguments == {"mode": "check_updated"}


@pytest.mark.parametrize(
    "message",
    ["run anyway", "use the current template as-is", "use current template as is"],
)
def test_only_an_explicit_override_uses_the_current_warned_design(message: str) -> None:
    decision = think(message, api_key="")

    assert decision.tool == "rebuild_flyer"
    assert decision.arguments == {"mode": "run_anyway"}


@pytest.mark.parametrize("message", ["try again", "check again", "I updated it", "use it as-is"])
def test_ambiguous_retry_language_does_not_guess_at_a_template_action(message: str) -> None:
    decision = think(message, api_key="")

    assert decision.tool == ""
