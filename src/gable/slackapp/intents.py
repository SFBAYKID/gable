"""Deterministic Slack intents that must not depend on model interpretation.

These are deliberately narrow continuations whose meaning is proven by the
owned thread's persisted state or immediately preceding question. Everything
else remains the conversation model's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

#: Ways a person asks for the same flyer to be built again. Chase's own wording
#: is "run it again", which the older set did not contain, so the request read
#: as ordinary conversation and nothing happened. Word-folded before matching,
#: so punctuation and capitalisation do not matter.
RUN_AGAIN_PHRASES: Final[frozenset[str]] = frozenset(
    {
        "yes run again",
        "run again",
        "run it again",
        "can you run it again",
        "please run it again",
        "run this again",
        "rerun this project",
        "can you rerun this project",
        "rerun this flyer",
        "can you rerun this flyer",
        "rebuild this flyer",
    }
)


def asks_to_run_again(text: str) -> bool:
    """Whether a message plainly asks for the flyer to be built again.

    Deliberately exact rather than fuzzy: this authorises replacing a delivered
    flyer, so it matches whole known phrasings and lets anything else fall
    through to the conversation model.

    Args:
        text: What the person said, as sent. A leading greeting is ignored.

    Returns:
        True for a recognised request.

    Raises:
        Nothing.
    """
    folded = _fold_words(text)
    for greeting in ("hey gable ", "hi gable ", "hey ", "hi ", "gable "):
        if folded.startswith(greeting):
            folded = folded.removeprefix(greeting)
            break
    return folded in RUN_AGAIN_PHRASES


@dataclass(frozen=True, slots=True)
class Decision:
    """What Gable concluded: something to say and optionally something to do."""

    reply: str
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_action(self) -> bool:
        """Return whether a tool was chosen rather than a plain answer."""
        return bool(self.tool)


def deterministic_decision(
    message: str,
    history: list[tuple[str, str]],
    context: str,
) -> Decision | None:
    """Resolve state-proven continuations before consulting a paid model."""
    decision = _rebuild_shortcut(message, history, context)
    if decision is not None:
        return decision
    decision = _photo_clarification_shortcut(message, history)
    if decision is not None:
        return decision
    return _paused_run_shortcut(message, context)


def _photo_clarification_shortcut(
    message: str,
    history: list[tuple[str, str]],
) -> Decision | None:
    """Resolve a terse answer only after Gable contrasted both photo targets."""
    if len(history) < 2:
        return None
    prior_speaker, prior_text = history[-2]
    gable_speaker, gable_text = history[-1]
    if prior_speaker.casefold() in {"gable", "assistant"}:
        return None
    if gable_speaker.casefold() not in {"gable", "assistant"}:
        return None

    prior = _fold_words(prior_text)
    question = _fold_words(gable_text)
    answer = _fold_words(message)
    asked_for_replacement = any(
        word in prior.split() for word in ("update", "replace", "different", "new", "other")
    ) and any(word in prior.split() for word in ("image", "photo", "picture"))
    contrasted_targets = "headshot" in question and any(
        phrase in question for phrase in ("large photo", "big photo", "hero", "property photo")
    )
    if not asked_for_replacement or not contrasted_targets:
        return None

    if answer in {"the big one", "big one", "the large one", "large one", "hero", "hero photo"}:
        return Decision(
            reply="Send me the new property photo.",
            tool="replace_photo",
            arguments={"which": "hero"},
        )
    if answer in {"headshot", "the headshot", "agent headshot", "the agent headshot"}:
        return Decision(
            reply="I will use the updated filed headshot.",
            tool="replace_photo",
            arguments={"which": "headshot"},
        )
    return None


def _fold_words(text: str) -> str:
    """Return lowercase space-separated alphanumeric words for narrow routing."""
    return " ".join(
        "".join(character if character.isalnum() else " " for character in text.casefold()).split()
    )


def _check_updated_decision() -> Decision:
    """Return the safe action for a source the person says they corrected."""
    return Decision(
        reply="I will reload and recheck the updated source template.",
        tool="rebuild_flyer",
        arguments={"mode": "check_updated"},
    )


def _paused_status(context: str) -> str:
    """Extract the internally generated run status from listing context."""
    prefix = "Run status: "
    for line in context.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).removesuffix(".").strip()
    return ""


def _source_correction_context(context: str) -> bool:
    """Return whether re-reading a source cannot overwrite a finished flyer."""
    status = _paused_status(context)
    return (
        "This thread is about the source template named " in context
        or status in {"needs_template", "needs_info"}
        or (status == "needs_photo" and "No flyer has been built in this thread yet." in context)
    )


def _rebuild_shortcut(
    message: str,
    history: list[tuple[str, str]],
    context: str,
) -> Decision | None:
    """Resolve source rechecks without exposing a layout-warning override."""
    folded = _fold_words(message)
    for greeting in ("hey gable ", "hi gable ", "hey ", "hi ", "gable "):
        if folded.startswith(greeting):
            folded = folded.removeprefix(greeting)
            break
    generic_rebuilds = set(RUN_AGAIN_PHRASES)
    retired_overrides = {
        "run anyway",
        "use the current template as is",
        "use current template as is",
    }
    if folded in generic_rebuilds | retired_overrides and _paused_status(context) == "needs_photo":
        return _photo_wait_decision(context)
    if folded in generic_rebuilds | {
        "i updated the template",
        "check the template again",
        "check the updated template",
    }:
        return _check_updated_decision()
    if folded in retired_overrides:
        return Decision(
            reply=(
                "I do not bypass template safety checks. If you updated the source design, "
                "tell me to check it again."
            )
        )

    correction_words = {"adjusted", "changed", "fixed", "resized", "updated", "widened"}
    retry_phrases = (
        "again",
        "retry",
        "rerun",
        "rebuild",
        "recheck",
        "check it",
        "try it",
    )
    template_fields = (
        "address",
        "agent name",
        "agent s name",
        "agents name",
        "email",
        "phone",
        "price",
        "text box",
        "template",
        "design",
    )
    words = set(folded.split())
    names_correction = bool(words & correction_words) and any(
        item in folded for item in template_fields
    )
    asks_to_retry = any(phrase in folded for phrase in retry_phrases)
    if names_correction and asks_to_retry and _source_correction_context(context):
        return _check_updated_decision()

    if folded in {"done", "fixed", "fixed it", "updated", "updated it"} and _paused_status(
        context
    ) in {"needs_template", "needs_info"}:
        return _check_updated_decision()

    latest_gable = ""
    if history and history[-1][0].casefold() in {"gable", "assistant"}:
        latest_gable = _fold_words(history[-1][1])
    names_only_a_field = folded in {
        "address",
        "the address",
        "agent name",
        "agent s name",
        "agents name",
        "the agent name",
        "email",
        "phone",
        "price",
    }
    asked_what_changed = any(
        phrase in latest_gable
        for phrase in ("what did you finish", "what did you change", "what did you update")
    )
    if names_only_a_field and asked_what_changed and _paused_status(context) == "needs_template":
        return _check_updated_decision()
    return None


def _paused_run_shortcut(message: str, context: str) -> Decision | None:
    """Keep photo-wait acknowledgements inside the persisted listing state."""
    if _paused_status(context) != "needs_photo":
        return None
    folded = _fold_words(message)
    if folded in {"ye", "yes", "yeah", "yep", "ok", "okay", "done"}:
        return _photo_wait_decision(context)
    if (
        folded
        in {
            "edit the existing one",
            "edit existing one",
            "edit the exiting one",
            "edit exiting one",
            "edit the existing flyer",
            "edit existing flyer",
        }
        and "No flyer has been built in this thread yet." in context
    ):
        return Decision(
            reply=(
                "There is no flyer to edit yet. Send me the property image in this "
                "thread and I will build it."
            )
        )
    return None


def _photo_wait_decision(context: str) -> Decision:
    """Restate the one image needed without rebuilding from a rejected source."""
    has_rejected_photo = (
        "A flyer exists in this thread." in context
        and "A human-supplied hero photo is attached to this run." in context
    )
    if has_rejected_photo:
        return Decision(reply="Send me the correct property image in this thread.")
    return Decision(reply="Send me the property image in this thread.")
