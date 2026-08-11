"""Understanding what Carmen said, and choosing what to do about it.

This is the cognitive half. A keyword matcher would answer "hello" with silence
and "can you make that bigger" with a guess; a model reads the sentence, picks a
tool, and asks when it is unsure.

Three things make this safe rather than merely clever:

1. **The model chooses a tool, it does not write the request.** It returns a
   name and arguments; `slides/edits.py` builds the Slides request. A
   hallucinated field cannot reach Google because the builder would not accept
   it.
2. **Every reply is checked before it is posted.** `style.violations()` runs on
   the model's own words. If the model produces a bracketed token or leaks an
   error string, the reply is replaced rather than sent.
3. **Not knowing is a valid answer.** The system prompt says so explicitly, and
   `ask_clarifying` is a first-class tool rather than a failure path.

Uses `GABLE_CONVERSATION_MODEL` (default `gpt-5-mini`) — chosen on cost, since
this is the highest-volume path in the product. See `.env.example`.

Does not handle: sending anything to Slack, or executing the tool it picks.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Final

from gable.slackapp.style import humanize_error, is_clean, strip_to_plain

#: OpenAI chat completions. Documented at
#: https://platform.openai.com/docs/api-reference/chat
_ENDPOINT: Final[str] = "https://api.openai.com/v1/chat/completions"

_TIMEOUT_SECONDS: Final[int] = 60

#: What Gable is and how it speaks. Deliberately long: the style rules are the
#: expensive part to get wrong, and repeating them here is cheaper than
#: repairing a message after the fact.
SYSTEM_PROMPT: Final[str] = """\
You are Gable. You turn real-estate listing requests into finished Google Slides
flyers for Carmen, a designer at Corner House Realty.

WHO YOU TALK TO
Carmen and Chase, in one Slack channel. Nobody else, ever.

HOW YOU SPEAK
- Plain English, the way a capable colleague writes. Short.
- Never use emoji. Not one.
- Never show brackets, placeholder tokens, code formatting, file paths, function
  names, error text, stack traces, or HTTP status codes. If something failed,
  say what you were doing and what went wrong in ordinary words.
- Never paste a URL. Describe the link.
- One idea per message.

WHAT YOU KNOW
- Agents submit a Google Form. Each row becomes a flyer.
- The request type on the form picks a template category: Just Listed, Just
  Sold, Open House, Under Contract, Coming Soon, Client Review, Meet the Agent,
  Neighborhood. There are 45 designs in Drive.
- You look up public facts yourself — beds, baths, square footage — from the
  address. You never ask Carmen for something a search could settle.
- You DO ask when something is contradictory or genuinely unknowable: a sold
  listing with no closing price, an open house with no time, or which photo to
  use.

HOW YOU BEHAVE
- Confirm before acting on anything ambiguous. "Make the image bigger" could
  mean the hero photo or the headshot; ask which.
- Never claim you did something you did not do.
- If you do not know, say so. That is a good answer, not a failure.
- When Carmen asks for a change to a flyer, choose the matching tool. When she
  is chatting, just reply.
"""

#: The tools the model may choose. Each maps to a real function; the schema is
#: what stops it inventing arguments.
TOOLS: Final[list[dict[str, Any]]] = [
    {
        "type": "function",
        "function": {
            "name": "set_font_size",
            "description": "Make the text in one element bigger or smaller.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Which text, in Carmen's words: price, address, agent name.",
                    },
                    "points": {"type": "number", "description": "New size in points."},
                },
                "required": ["target", "points"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_colour",
            "description": "Recolour text, a shape's fill, or a divider line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "What to recolour."},
                    "colour": {"type": "string", "description": "A colour name or hex."},
                },
                "required": ["target", "colour"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resize_photo",
            "description": "Make the hero photo or the headshot larger or smaller.",
            "parameters": {
                "type": "object",
                "properties": {
                    "which": {
                        "type": "string",
                        "enum": ["hero", "headshot"],
                        "description": "Which image. Ask first if the request is ambiguous.",
                    },
                    "factor": {
                        "type": "number",
                        "description": "1.2 is a noticeable increase, 0.8 a decrease.",
                    },
                },
                "required": ["which", "factor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_element",
            "description": "Nudge one unambiguous text element or photo left, right, up, or down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "What to move, such as hero photo, headshot, price, or address."
                        ),
                    },
                    "dx_points": {
                        "type": "number",
                        "description": "Horizontal move in points. Positive moves right.",
                    },
                    "dy_points": {
                        "type": "number",
                        "description": "Vertical move in points. Positive moves down.",
                    },
                },
                "required": ["target", "dx_points", "dy_points"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correct_field",
            "description": "Replace a wrong value on the flyer, such as a phone number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "current": {"type": "string", "description": "The value on the flyer now."},
                    "replacement": {"type": "string", "description": "What it should say."},
                },
                "required": ["current", "replacement"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rebuild_flyer",
            "description": "Start the flyer again from the template, keeping the data.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_clarifying",
            "description": (
                "Ask Carmen a question instead of guessing. Use this whenever a request "
                "could reasonably mean two different things."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question, in plain words."}
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_status",
            "description": "Say what Gable is currently doing or waiting on.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


@dataclass(frozen=True, slots=True)
class Decision:
    """What the model concluded: something to say, and optionally something to do."""

    reply: str
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_action(self) -> bool:
        """True when a tool was chosen rather than a plain answer."""
        return bool(self.tool)


def _post(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    """One call to the chat endpoint.

    Args:
        payload: The request body.
        api_key: The OpenAI key.

    Returns:
        The decoded response.

    Raises:
        urllib.error.URLError: on a transport failure.
        urllib.error.HTTPError: on a non-2xx response.
    """
    request = urllib.request.Request(
        _ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        decoded: dict[str, Any] = json.loads(response.read())
        return decoded


def think(
    message: str,
    history: list[tuple[str, str]] | None = None,
    context: str = "",
    api_key: str | None = None,
    model: str | None = None,
) -> Decision:
    """Decide what to say, and whether to reach for a tool.

    Args:
        message: What Carmen just wrote, with the mention already stripped.
        history: Earlier turns as `(speaker, text)`, oldest first.
        context: Facts about the listing under discussion, if any.
        api_key: OpenAI key. Defaults to the environment.
        model: Override the configured conversation model.

    Returns:
        A `Decision`. Its `reply` is always safe to post: if the model produced
        something that breaks the house style, it is scrubbed, and if it is
        still unsafe it is replaced entirely.

    Raises:
        Nothing. This runs in a Slack handler; a raised exception there is a
        message Carmen never receives. Every failure becomes a plain sentence.
    """
    key = api_key or os.environ.get("OPENAI_IMAGE_API_KEY", "")
    if not key:
        return Decision(
            reply=(
                "I can't think about that right now — my language model isn't configured. "
                "Chase will need to set that up."
            )
        )

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": f"About the listing in hand:\n{context}"})
    for speaker, text in history or []:
        role = "assistant" if speaker.lower() in {"gable", "assistant"} else "user"
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": message})

    payload = {
        "model": model or os.environ.get("GABLE_CONVERSATION_MODEL", "gpt-5-mini"),
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "max_completion_tokens": 2000,
    }

    try:
        body = _post(payload, key)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return Decision(reply=humanize_error(raw, "working out what you meant"))
    except Exception as exc:
        return Decision(reply=humanize_error(str(exc), "working out what you meant"))

    choice = (body.get("choices") or [{}])[0].get("message", {})
    said = (choice.get("content") or "").strip()
    calls = choice.get("tool_calls") or []

    tool_name, arguments = "", {}
    if calls:
        call = calls[0].get("function", {})
        tool_name = str(call.get("name", ""))
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError:
            # A malformed argument blob is the model's problem, not Carmen's.
            arguments = {}
        if tool_name == "ask_clarifying" and not said:
            said = str(arguments.get("question", "")).strip()
        elif not said:
            # A model that picks an action tool often returns no prose with it.
            # Saying "I'm not sure what you meant" while confidently calling
            # set_font_size is the worst of both — it reads as confusion and
            # then acts anyway.
            said = acknowledge(tool_name, arguments)

    if not said:
        said = "I'm not sure what you'd like me to do there. Could you say a bit more?"

    return Decision(reply=_make_safe(said), tool=tool_name, arguments=arguments)


#: What Gable says when it has chosen an action and the model supplied no words.
_ACKNOWLEDGEMENTS: Final[dict[str, str]] = {
    "set_font_size": "Making the {target} {direction}.",
    "set_colour": "Changing the {target} to {colour}.",
    "resize_photo": "Making the {which} photo {direction}.",
    "move_element": "Moving the {target}.",
    "correct_field": "Changing that to {replacement}.",
    "rebuild_flyer": "Starting the flyer again from the template.",
    "report_status": "Let me check where things stand.",
}


def acknowledge(tool: str, arguments: dict[str, Any]) -> str:
    """A sentence saying what Gable is about to do.

    Args:
        tool: The chosen tool name.
        arguments: Its arguments.

    Returns:
        Plain words. Falls back to a generic sentence for an unknown tool rather
        than exposing the tool's name, which is developer language.

    Raises:
        Nothing.
    """
    template = _ACKNOWLEDGEMENTS.get(tool)
    if not template:
        return "On it."
    factor = arguments.get("factor") or arguments.get("points")
    direction = "bigger"
    if isinstance(factor, (int, float)) and factor < 1:
        direction = "smaller"
    try:
        return template.format(
            target=arguments.get("target", "that"),
            colour=arguments.get("colour", "the new colour"),
            which=arguments.get("which", "hero"),
            replacement=arguments.get("replacement", "the new value"),
            direction=direction,
        )
    except (KeyError, IndexError):
        return "On it."


def _make_safe(text: str) -> str:
    """Guarantee a reply obeys the house style before it is posted.

    A model told not to use emoji will mostly not use emoji. "Mostly" is not a
    guarantee, and this is the last point at which a breach can be stopped.

    Args:
        text: The model's words.

    Returns:
        The text if it is already clean, a scrubbed version if scrubbing is
        enough, or a plain fallback if it is not.

    Raises:
        Nothing.
    """
    if is_clean(text):
        return text
    scrubbed = strip_to_plain(text)
    if is_clean(scrubbed):
        return scrubbed
    return (
        "I had trouble putting that answer into words. Could you ask me again, "
        "or tell me which part of the flyer you mean?"
    )
