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

Uses `GABLE_CONVERSATION_MODEL` (default `gpt-5.6-sol`). The hard spend ledger
still bounds the path, but ambiguous flyer edits and source-template decisions
use the same flagship reasoning tier as the visual gate because a cheap wrong
action costs more than this low-volume conversation call. See `.env.example`.

Does not handle: sending anything to Slack, or executing the tool it picks.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Final

from gable.slackapp.style import is_clean, strip_to_plain

#: GPT-5.6 reasoning with function tools belongs on Responses. Chat Completions
#: rejects that combination unless reasoning is disabled, which silently took
#: Gable's most important ambiguity handling offline.
_ENDPOINT: Final[str] = "https://api.openai.com/v1/responses"

_TIMEOUT_SECONDS: Final[int] = 60
_DEFAULT_MODEL: Final[str] = "gpt-5.6-sol"
logger = logging.getLogger("gable.slack.brain")

#: What Gable is and how it speaks. Deliberately long: the style rules are the
#: expensive part to get wrong, and repeating them here is cheaper than
#: repairing a message after the fact.
SYSTEM_PROMPT: Final[str] = """\
You are Gable. You turn real-estate listing requests into finished Google Slides
flyers for Carmen, a designer at Corner House Realty.

WHO YOU TALK TO
Carmen and Chase, in one Slack channel. Nobody else, ever. When you are told who
is speaking, address that person by their first name and nobody else's. Greeting
a room when one person asked you something reads as though you are not listening.

HOW YOU SPEAK
- Plain English, the way a capable colleague writes in Slack.
- SHORT. Three sentences at most, and usually one or two. This is a chat
  message, not a document. If your answer needs a heading, it is too long.
- Never use bullet lists, numbered lists, headings, or bold labels. Write
  sentences. A wall of text does not get read, so it does not count as an answer.
- If someone asks what you need, name the two or three things that actually
  matter and stop. Do not enumerate every field that could exist.
- Never use emoji. Not one.
- Never show brackets, placeholder tokens, code formatting, file paths, function
  names, error text, stack traces, or HTTP status codes. If something failed,
  say what you were doing and what went wrong in ordinary words.
- Never paste a URL. Describe the link.
- One idea per message.

WHAT YOU KNOW
- Agents submit a Google Form. Each row becomes a flyer.
- The request type on the form picks the template. Templates live only in the
  shared Generic Templates folder and the file name must match the request type.
  If no matching template is filed, you say so and stop. You never substitute
  a different design.
- The submitted name and email identify the agent. Their phone comes from the
  contact workbook and their headshot comes from Head Shots. If either source
  is missing something the chosen design needs, name what is missing and tell
  Carmen or Chase where to add it; never invent or web-correct it.
- You look up public facts yourself — beds, baths, square footage — from the
  address.
- You DO ask when something is contradictory or genuinely unknowable: a price
  reduction with no new price, an open house with no time, or which photo to
  use. A sold request may omit its closing price only when its chosen design has
  no price field; Gable's preflight names the missing field before building.

WHAT YOU CANNOT DO — never offer any of these
- You have no MLS access. You cannot pull MLS photos, MLS numbers, or listing
  history. The only photo you get is the one someone sends you.
- You cannot choose a photo for someone, or "pick the best" one. You are not
  shown a set to choose from. Ask for the image.
- You do not redesign a source template to taste. You can apply an explicit,
  unambiguous correction to a flyer already built in this thread. Carmen edits
  source templates; after she does, you can read the current file and check or
  rebuild from it.
- You cannot estimate or guess a price, a date, or any fact about a property.
- Offering something on this list is worse than saying you cannot do it, because
  someone will be waiting for a thing that is never coming.

HOW YOU BEHAVE
- Confirm before acting on anything ambiguous. "Make the image bigger" could
  mean the hero photo or the headshot; ask which.
- Never claim you did something you did not do.
- If you do not know, say so. That is a good answer, not a failure.
- Tools change a flyer that already exists in the current thread. If this thread
  has no flyer in it, there is nothing for a tool to act on: answer in words.
  "Can you start today", "what do you need", "what can you do" are conversation,
  not instructions — reaching for a tool there produces "I could not match this
  thread to a listing", which reads as a malfunction in reply to a plain
  question.
- When Carmen asks for a change to a flyer, choose the matching tool. When she
  is chatting, just reply.
- If Gable warned that template content may not fit, "run anyway" means rebuild
  with the current template and accept only non-blocking preflight warnings.
  "Yes, run again" means reload and recheck the current source template. Treat
  a vague reply such as "try again", "check again", or "I updated it" as
  ambiguous unless the person names the template. Never treat an unreadable or
  structurally unsafe template as overridable.
- Before you offer to do something, check it against WHAT YOU CANNOT DO. An
  eager answer that promises the impossible costs more than a short honest one.
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
            "description": (
                "Reload the current source template and rebuild with the saved listing data. "
                "Use check_updated after Carmen edits it; use run_anyway only when she "
                "explicitly accepts a non-blocking fit or crop warning."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["check_updated", "run_anyway"],
                    }
                },
                "required": ["mode"],
            },
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
    """One call to the Responses endpoint.

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


def _response_tools() -> list[dict[str, Any]]:
    """Translate the local tool catalogue into the Responses API shape."""
    result: list[dict[str, Any]] = []
    for tool in TOOLS:
        function = tool.get("function", {})
        result.append(
            {
                "type": "function",
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
            }
        )
    return result


def _response_result(body: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Extract reply text and the first direct function call from Responses."""
    if body.get("status") != "completed":
        return "", "", {}
    text_parts: list[str] = []
    tool_name = ""
    arguments: dict[str, Any] = {}
    for item in body.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call" and not tool_name:
            tool_name = str(item.get("name") or "")
            raw_arguments = item.get("arguments") or "{}"
            try:
                parsed = json.loads(raw_arguments) if isinstance(raw_arguments, str) else {}
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                arguments = parsed
            continue
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                return "", "", {}
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                text_parts.append(str(content["text"]))
    return "".join(text_parts).strip(), tool_name, arguments


def _provider_unavailable() -> Decision:
    """Return one provider-specific refusal that cannot blame Google Slides."""
    return Decision(
        reply=(
            "I couldn't finish working out what you meant because the language model "
            "was unavailable. I did not change the flyer. Try again."
        )
    )


def think(
    message: str,
    history: list[tuple[str, str]] | None = None,
    context: str = "",
    api_key: str | None = None,
    model: str | None = None,
    speaker: str = "",
) -> Decision:
    """Decide what to say, and whether to reach for a tool.

    Args:
        message: What Carmen just wrote, with the mention already stripped.
        history: Earlier turns as `(speaker, text)`, oldest first.
        context: Facts about the listing under discussion, if any.
        speaker: The first name of whoever is talking, when known. Passed so a
            greeting names the person who actually asked rather than listing
            everyone who might be in the channel.
        api_key: OpenAI key passed by validated runtime configuration.
        model: Override the configured conversation model.

    Returns:
        A `Decision`. Its `reply` is always safe to post: if the model produced
        something that breaks the house style, it is scrubbed, and if it is
        still unsafe it is replaced entirely.

    Raises:
        Nothing. This runs in a Slack handler; a raised exception there is a
        message Carmen never receives. Every failure becomes a plain sentence.
    """
    shortcut = _rebuild_shortcut(message)
    if shortcut is not None:
        return shortcut

    key = api_key or ""
    if not key:
        return Decision(
            reply=(
                "I can't think about that right now — my language model isn't configured. "
                "Chase will need to set that up."
            )
        )

    instructions = [SYSTEM_PROMPT]
    if speaker:
        instructions.append(f"You are speaking to {speaker}. Use their name.")
    if context:
        instructions.append(f"About the listing in hand:\n{context}")
    messages: list[dict[str, Any]] = []
    for who, text in history or []:
        role = "assistant" if who.lower() in {"gable", "assistant"} else "user"
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": message})

    payload = {
        "model": model or _DEFAULT_MODEL,
        "instructions": "\n\n".join(instructions),
        "input": messages,
        "tools": _response_tools(),
        "tool_choice": "auto",
        "reasoning": {"effort": "medium"},
        "text": {"verbosity": "low"},
        "max_output_tokens": 2000,
        "store": False,
    }

    try:
        body = _post(payload, key)
    except urllib.error.HTTPError as exc:
        # Do not read or log the body. Provider failures can echo request data,
        # and no raw error belongs in Slack or the journal.
        logger.warning("conversation model returned HTTP %s", exc.code)
        return _provider_unavailable()
    except Exception:
        logger.exception("the conversation model could not be reached")
        return _provider_unavailable()

    said, tool_name, arguments = _response_result(body)
    if body.get("status") != "completed":
        logger.warning("conversation model returned an incomplete response")
        return _provider_unavailable()
    if not said and not tool_name:
        logger.warning("conversation model returned no usable text or function call")
        return _provider_unavailable()

    if tool_name:
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


def _rebuild_shortcut(message: str) -> Decision | None:
    """Resolve the two template-warning answers without probabilistic routing.

    "Yes, run again" is the product's promise that Gable reloads the current
    Drive source after Carmen changes it. "Run anyway" means the opposite: she
    accepts the current source's nonblocking warning. Confusing them either
    ignores her correction or bypasses it, so these small explicit phrases do
    not need a paid model call.
    """
    words_only = "".join(
        character if character.isalnum() or character.isspace() else " "
        for character in message.casefold()
    )
    folded = " ".join(words_only.split())
    for greeting in ("hey gable ", "hi gable ", "hey ", "hi ", "gable "):
        if folded.startswith(greeting):
            folded = folded.removeprefix(greeting)
            break
    if folded in {
        "yes run again",
        "run again",
        "rerun this project",
        "can you rerun this project",
        "rerun this flyer",
        "can you rerun this flyer",
        "rebuild this flyer",
        "i updated the template",
        "check the template again",
        "check the updated template",
    }:
        return Decision(
            reply="I will reload the updated source template.",
            tool="rebuild_flyer",
            arguments={"mode": "check_updated"},
        )
    if folded in {
        "run anyway",
        "use the current template as is",
        "use current template as is",
    }:
        return Decision(
            reply="I will use the current design as it is.",
            tool="rebuild_flyer",
            arguments={"mode": "run_anyway"},
        )
    return None


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
