"""Unambiguous template-triage replies never depend on model interpretation."""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from gable.slackapp import brain
from gable.slackapp.brain import think
from gable.voice import MAX_REPLY_CHARS


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


NEEDS_PHOTO_CONTEXT = """\
Run status: needs_photo.
Request type: Sold.
Property address: 703 Perception Way, Aberdeen, MD 21001.
Submitting agent: Mike Kulnich.
Selected template: Sold.
No flyer has been built in this thread yet.
This run has no hero photo yet.
The run is waiting because: Can you send me the image?
"""


NEEDS_TEMPLATE_CONTEXT = """\
Run status: needs_template.
Request type: Sold.
Selected template: Sold.
No flyer has been built in this thread yet.
This run has no hero photo yet.
The run is waiting because: The agent name needs about 5 percent more room.
"""


REPLACEMENT_PHOTO_CONTEXT = """\
Run status: needs_photo.
Request type: Sold.
Property address: 703 Perception Way, Aberdeen, MD 21001.
Submitting agent: Mike Kulnich.
Selected template: Sold.
A flyer exists in this thread.
A human-supplied hero photo is attached to this run.
The run is waiting because: the supplied photo conflicts with the listing.
"""


@pytest.mark.parametrize(
    "message",
    ["run anyway", "use the current template as-is", "use current template as is"],
)
def test_run_anyway_cannot_bypass_a_template_safety_stop(message: str) -> None:
    decision = think(message, context=NEEDS_TEMPLATE_CONTEXT, api_key="")

    assert decision.tool == ""
    assert "do not bypass" in decision.reply


@pytest.mark.parametrize(
    "message",
    ["run anyway", "run again", "Hey, can you rerun this project?"],
)
def test_any_rerun_wording_while_waiting_for_the_first_photo_restates_the_request(
    message: str,
) -> None:
    decision = think(message, context=NEEDS_PHOTO_CONTEXT, api_key="")

    assert decision.tool == ""
    assert decision.reply == "Send me the property image in this thread."


def test_a_named_source_correction_rechecks_instead_of_becoming_a_flyer_edit() -> None:
    decision = think(
        "I adjusted the size of the address can you do again?",
        context=NEEDS_PHOTO_CONTEXT,
        api_key="",
    )

    assert decision.tool == "rebuild_flyer"
    assert decision.arguments == {"mode": "check_updated"}
    assert "reload and recheck" in decision.reply


def test_the_same_words_do_not_overwrite_a_finished_flyer_without_source_context() -> None:
    decision = think(
        "I adjusted the size of the address can you do again?",
        context=(
            "Run status: delivered.\nSelected template: Sold.\nA flyer exists in this thread."
        ),
        api_key="",
    )

    assert decision.tool == ""


@pytest.mark.parametrize("message", ["Done", "fixed it", "updated it"])
def test_a_completed_paused_template_correction_rechecks_without_an_interview(
    message: str,
) -> None:
    decision = think(message, context=NEEDS_TEMPLATE_CONTEXT, api_key="")

    assert decision.tool == "rebuild_flyer"
    assert decision.arguments == {"mode": "check_updated"}


@pytest.mark.parametrize("message", ["agents name", "agent's name"])
def test_the_named_field_completes_gables_prior_source_correction_question(
    message: str,
) -> None:
    decision = think(
        message,
        history=[
            ("Chase", "Done"),
            ("Gable", "What did you finish — the template, listing data, or images?"),
        ],
        context=NEEDS_TEMPLATE_CONTEXT,
        api_key="",
    )

    assert decision.tool == "rebuild_flyer"
    assert decision.arguments == {"mode": "check_updated"}


@pytest.mark.parametrize("message", ["ye", "yes", "okay"])
def test_a_terse_acknowledgement_in_a_photo_wait_repeats_the_only_missing_step(
    message: str,
) -> None:
    decision = think(message, context=NEEDS_PHOTO_CONTEXT, api_key="")

    assert decision.tool == ""
    assert decision.reply == "Send me the property image in this thread."


@pytest.mark.parametrize(
    "message",
    ["run again", "Hey, can you rerun this project?", "Can you rerun this flyer?"],
)
def test_a_rerun_request_cannot_rebuild_from_a_rejected_photo(message: str) -> None:
    decision = think(message, context=REPLACEMENT_PHOTO_CONTEXT, api_key="")

    assert decision.tool == ""
    assert decision.reply == "Send me the correct property image in this thread."


@pytest.mark.parametrize("message", ["Edit the existing one", "Edit the exiting one"])
def test_edit_existing_does_not_invent_a_flyer_while_the_run_waits_for_its_first_photo(
    message: str,
) -> None:
    decision = think(message, context=NEEDS_PHOTO_CONTEXT, api_key="")

    assert decision.tool == ""
    assert decision.reply == (
        "There is no flyer to edit yet. Send me the property image in this thread and I will "
        "build it."
    )


def test_the_big_one_resolves_only_from_the_immediately_preceding_photo_clarification() -> None:
    decision = think(
        "the big one",
        history=[
            ("Chase", "update the image"),
            ("Gable", "Did you mean the large photo or Mike's headshot?"),
        ],
        api_key="",
    )

    assert decision.tool == "replace_photo"
    assert decision.arguments == {"which": "hero"}
    assert decision.reply == "Send me the new property photo."


def test_the_headshot_resolves_only_from_the_immediately_preceding_photo_clarification() -> None:
    decision = think(
        "the headshot",
        history=[
            ("Chase", "replace the photo"),
            ("Gable", "Did you mean the large property photo or the headshot?"),
        ],
        api_key="",
    )

    assert decision.tool == "replace_photo"
    assert decision.arguments == {"which": "headshot"}


def test_a_free_standing_big_one_is_never_guessed_as_a_photo_action() -> None:
    decision = think("the big one", api_key="")

    assert decision.tool == ""


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


def test_model_reply_uses_the_same_slack_length_and_format_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prompt compliance is not the mechanism that contains a wall of text."""
    wordy = "## What I need\n" + "\n".join("- Send the property image." for _ in range(80))
    monkeypatch.setattr(
        brain,
        "_post",
        lambda _payload, _key: {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": wordy}],
                }
            ],
        },
    )

    decision = think("What do you need?", api_key="test-key")

    assert len(decision.reply) <= MAX_REPLY_CHARS
    assert "##" not in decision.reply
    assert "- " not in decision.reply
