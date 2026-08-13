"""Unambiguous template-triage replies never depend on model interpretation."""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from gable.slackapp import brain
from gable.slackapp.brain import think


@pytest.mark.parametrize(
    "message",
    [
        "yes, run again",
        "run again",
        "Hey, can you rerun this project?",
        "Can you rerun this flyer?",
        "Rebuild this flyer",
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


def test_conversation_uses_responses_with_reasoning_and_direct_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def post(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        seen.update(payload)
        assert api_key == "test-key"
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "I can help with that."}],
                }
            ],
        }

    monkeypatch.setattr(brain, "_post", post)

    decision = think(
        "What can you do?",
        history=[("Carmen", "Hello"), ("Gable", "Hi")],
        context="This is the Sold listing.",
        api_key="test-key",
        model="gpt-5.6-sol",
        speaker="Carmen",
    )

    assert decision.reply == "I can help with that."
    assert seen["model"] == "gpt-5.6-sol"
    assert seen["reasoning"] == {"effort": "medium"}
    assert seen["text"] == {"verbosity": "low"}
    assert seen["store"] is False
    assert "Carmen" in seen["instructions"]
    assert "Sold listing" in seen["instructions"]
    assert [item["role"] for item in seen["input"]] == ["user", "assistant", "user"]
    assert all(tool["type"] == "function" for tool in seen["tools"])
    assert all("function" not in tool for tool in seen["tools"])
    assert {tool["name"] for tool in seen["tools"]} >= {
        "ask_clarifying",
        "rebuild_flyer",
    }


def test_responses_function_call_is_parsed_without_chat_completion_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        brain,
        "_post",
        lambda _payload, _key: {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "set_font_size",
                    "arguments": '{"target":"address","points":18}',
                }
            ],
        },
    )

    decision = think("Make the address 18 points", api_key="test-key")

    assert decision.tool == "set_font_size"
    assert decision.arguments == {"target": "address", "points": 18}
    assert decision.reply


def test_conversation_provider_failure_never_blames_google_or_leaks_raw_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_payload: dict[str, Any], _key: str) -> dict[str, Any]:
        raise urllib.error.HTTPError(
            "https://api.openai.com/v1/responses",
            400,
            "raw provider detail",
            None,  # type: ignore[arg-type]  # stdlib permits omitted headers
            None,
        )

    monkeypatch.setattr(brain, "_post", fail)

    decision = think("Make the address larger", api_key="test-key")

    assert "language model was unavailable" in decision.reply
    assert "Google" not in decision.reply
    assert "raw provider detail" not in decision.reply
    assert decision.tool == ""


def test_incomplete_conversation_response_fails_plainly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(brain, "_post", lambda _payload, _key: {"status": "incomplete"})

    decision = think("Make the address larger", api_key="test-key")

    assert "language model was unavailable" in decision.reply
    assert decision.tool == ""
