"""Looking at the rendered flyer before delivering it.

The text checks in `orchestrator.judge` verify that every value is *present*.
They cannot see whether it *fits*, and that gap shipped a real flyer whose price
read `$510,000` in the document and rendered clipped to `$510,00`. Every API
call returned 200.

This closes it: render the slide to an image, hand it to a vision model, and ask
the question a designer would ask — does this look right? The model is told to
be specific and to say when it is unsure, because "looks fine" from a model that
did not really look is worse than no check.

Uses `GABLE_VISION_MODEL` (default `gpt-5.6-sol`) through the Responses API with
original image detail and a strict JSON schema. One high-quality call is made
per candidate flyer; an unavailable or inconclusive check blocks delivery.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Final

from gable.voice import safe

logger = logging.getLogger("gable.vision")

_ENDPOINT: Final[str] = "https://api.openai.com/v1/responses"
_TIMEOUT_SECONDS: Final[int] = 90
_DEFAULT_MODEL: Final[str] = "gpt-5.6-sol"

#: What the model is asked. Names the specific failures worth catching, because
#: "does this look good" invites a compliment rather than an inspection.
PROMPT: Final[str] = """\
You are checking a real-estate flyer before it is sent to a designer. The final
image is the rendered flyer. When a source image is also present, it appears
first and is the real property photograph supplied by a person; compare the
property in it with the photo placed in the final flyer. Answer only about what
you can actually see.

Report a problem if any of these is true:
- Text is cut off, clipped at a box edge, or runs off the slide.
- Text overlaps other text, an icon, a divider line, or the edge of a panel.
- A line of text wraps in a way that collides with the element below it.
- Any required listing or agent text is visibly too small to read at normal
  flyer viewing size, even when it technically fits inside its box.
- A visible placeholder remains, such as a word in square brackets, or wording
  like PROPERTY ADDRESS, AGENT NAME, Phone, Website or Email left as a label.
- The main photo area is empty, or shows a generic placeholder illustration
  rather than a photograph.
- The main photo is visibly stretched, squashed, badly pixelated, or cropped
  through an important part of the house such as its roofline or front facade.
- When a source photo is present, the flyer changes the property's identity,
  building geometry, windows, doors, signs, visible text, or overall scene.
  Normal resizing and an edge crop needed for the frame are acceptable when the
  same property and important composition remain clear.
- Something is obviously misaligned or overlapping in a way a designer would fix.

Do NOT report: style preferences, colour choices, or anything you are guessing at.

Reply as JSON only:
{"looks_right": true|false, "confident": true|false, "problems": ["..."]}

Each problem must be one short sentence naming what and where, in plain words a
designer would use. No coordinates, no element ids, no technical terms.
"""

TEMPLATE_PROMPT: Final[str] = """\
You are reviewing a reusable real-estate flyer source template before any real
listing is built from it. Judge only visible layout structure and legibility.

Report a problem if any of these is true:
- Text is clipped, cut off, outside its box, or off the slide.
- Text, shapes, icons, dividers, or image areas visibly overlap by accident.
- Related elements have conspicuously inconsistent spacing, alignment, or
  padding that makes the layout look broken rather than intentionally styled.
- Text is visibly too small to read or a section is visibly too cramped for its
  current sample content.
- A photo area or other major element extends off canvas or leaves an obviously
  unintended gap.

Intentional placeholder wording, sample contact data, and a generic sample
photo are allowed in a source template. Do not report them. Do not guess how
future replacement text will fit; exact box-capacity checks happen separately.
Do not critique colours, fonts, branding, or subjective design taste.

Reply as JSON only:
{"looks_right": true|false, "confident": true|false, "problems": ["..."]}

Each problem must be one short sentence naming what and where, in plain words a
designer would use. No coordinates, no element ids, no technical terms.
"""

_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "looks_right": {"type": "boolean"},
        "confident": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["looks_right", "confident", "problems"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Inspection:
    """What the vision pass concluded."""

    looks_right: bool
    confident: bool
    problems: list[str] = field(default_factory=list)
    #: True when the check itself could not run. Distinct from "it looks wrong",
    #: because the honest response to each is different.
    checked: bool = True

    @property
    def say(self) -> str:
        """What Gable tells Carmen, or an empty string when all is well."""
        if not self.checked:
            return ""
        if self.looks_right:
            return ""
        first = self.problems[0].strip() if self.problems else "something looks off on it"
        first = first or "something looks off on it"
        opener = "I rendered it, but" if self.confident else "I rendered it, and I think"
        return safe(f"{opener} {first[0].lower()}{first[1:]}")


def _post(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    """One call to the Responses endpoint."""
    request = urllib.request.Request(
        _ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        decoded: dict[str, Any] = json.loads(response.read())
        return decoded


def parse(reply: str) -> Inspection:
    """Read the model's answer, tolerating the ways models wrap JSON.

    Args:
        reply: The model's raw text.

    Returns:
        An `Inspection`. An unparseable reply is treated as "could not check"
        rather than as a pass — a check that silently degrades to approval is
        worse than no check.

    Raises:
        Nothing.
    """
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return Inspection(looks_right=False, confident=False, checked=False)
    if not isinstance(data, dict):
        return Inspection(looks_right=False, confident=False, checked=False)
    looks_right = data.get("looks_right")
    confident = data.get("confident")
    raw_problems = data.get("problems")
    if (
        not isinstance(looks_right, bool)
        or not isinstance(confident, bool)
        or not isinstance(raw_problems, list)
        or any(not isinstance(problem, str) for problem in raw_problems)
    ):
        return Inspection(looks_right=False, confident=False, checked=False)
    problems = [problem.strip() for problem in raw_problems if problem.strip()]
    if looks_right and problems:
        looks_right = False
    return Inspection(
        looks_right=looks_right,
        confident=confident,
        problems=problems,
    )


def _output_text(body: dict[str, Any]) -> str:
    """Join non-refusal text items from a completed Responses payload."""
    if body.get("status") != "completed":
        return ""
    parts: list[str] = []
    for output in body.get("output", []):
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for item in output.get("content", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "refusal":
                return ""
            if item.get("type") == "output_text" and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
    return "".join(parts)


def _inspect(
    image_bytes: bytes,
    prompt: str,
    api_key: str | None = None,
    model: str | None = None,
    reference_image_bytes: bytes = b"",
) -> Inspection:
    """Run one strict, fail-closed visual inspection request."""
    key = api_key or ""
    if not key or not image_bytes:
        return Inspection(looks_right=False, confident=False, checked=False)

    encoded = base64.b64encode(image_bytes).decode()
    content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
    if reference_image_bytes:
        reference = base64.b64encode(reference_image_bytes).decode()
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{reference}",
                "detail": "original",
            }
        )
    content.append(
        {
            "type": "input_image",
            "image_url": f"data:image/png;base64,{encoded}",
            "detail": "original",
        }
    )
    payload = {
        "model": model or _DEFAULT_MODEL,
        "input": [{"role": "user", "content": content}],
        "reasoning": {"effort": "high"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "flyer_inspection",
                "schema": _SCHEMA,
                "strict": True,
            }
        },
        "max_output_tokens": 1000,
    }
    try:
        body = _post(payload, key)
    except Exception:
        logger.exception("the vision check could not run")
        return Inspection(looks_right=False, confident=False, checked=False)

    return parse(_output_text(body))


def inspect(
    image_bytes: bytes,
    api_key: str | None = None,
    model: str | None = None,
    reference_image_bytes: bytes = b"",
) -> Inspection:
    """Ask a vision model whether a rendered flyer looks right.

    Args:
        image_bytes: A PNG or JPEG of the rendered slide.
        api_key: OpenAI key passed by validated runtime configuration.
        model: Override the configured vision model.
        reference_image_bytes: The person's original property photo, when
            available. It is compared with the photo visible in the flyer in
            the same call, so enhancement drift cannot pass as good layout.

    Returns:
        An `Inspection`. Never raises: this runs on the delivery path, and a
        failed check must return a recorded outcome rather than raising out of
        the run. The runner treats ``checked=False`` as a delivery blocker,
        preserving the distinction between a bad flyer and a check that could
        not prove anything.

    Raises:
        Nothing.
    """
    return _inspect(
        image_bytes,
        PROMPT,
        api_key=api_key,
        model=model,
        reference_image_bytes=reference_image_bytes,
    )


def inspect_template(
    image_bytes: bytes,
    api_key: str | None = None,
    model: str | None = None,
) -> Inspection:
    """Inspect a reusable source design without flagging its placeholders."""
    return _inspect(image_bytes, TEMPLATE_PROMPT, api_key=api_key, model=model)
