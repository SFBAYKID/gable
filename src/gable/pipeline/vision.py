"""Looking at the rendered flyer before delivering it.

The text checks in `orchestrator.judge` verify that every value is *present*.
They cannot see whether it *fits*, and that gap shipped a real flyer whose price
read `$510,000` in the document and rendered clipped to `$510,00`. Every API
call returned 200.

This closes it: render the slide to an image, hand it to a vision model, and ask
the question a designer would ask — does this look right? The model is told to
be specific and to say when it is unsure, because "looks fine" from a model that
did not really look is worse than no check.

Uses `GABLE_VISION_MODEL` (default `gpt-5-mini`, which has vision). One call per
delivered flyer, so the cost is a fraction of a cent.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Final

from gable.voice import safe

logger = logging.getLogger("gable.vision")

_ENDPOINT: Final[str] = "https://api.openai.com/v1/chat/completions"
_TIMEOUT_SECONDS: Final[int] = 90

#: What the model is asked. Names the specific failures worth catching, because
#: "does this look good" invites a compliment rather than an inspection.
PROMPT: Final[str] = """\
You are checking a real-estate flyer before it is sent to a designer. Look at the
image and answer only about what you can actually see.

Report a problem if any of these is true:
- Text is cut off, clipped at a box edge, or runs off the slide.
- Text overlaps other text, an icon, a divider line, or the edge of a panel.
- A line of text wraps in a way that collides with the element below it.
- A visible placeholder remains, such as a word in square brackets, or wording
  like PROPERTY ADDRESS, AGENT NAME, Phone, Website or Email left as a label.
- The main photo area is empty, or shows a generic placeholder illustration
  rather than a photograph.
- Something is obviously misaligned or overlapping in a way a designer would fix.

Do NOT report: style preferences, colour choices, or anything you are guessing at.

Reply as JSON only:
{"looks_right": true|false, "confident": true|false, "problems": ["..."]}

Each problem must be one short sentence naming what and where, in plain words a
designer would use. No coordinates, no element ids, no technical terms.
"""


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
        first = self.problems[0] if self.problems else "something looks off on it"
        opener = "I rendered it, but" if self.confident else "I rendered it, and I think"
        return safe(f"{opener} {first[0].lower()}{first[1:]}")


def _post(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    """One call to the chat endpoint."""
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
    problems = [str(p) for p in data.get("problems", []) if str(p).strip()]
    return Inspection(
        looks_right=bool(data.get("looks_right", False)),
        confident=bool(data.get("confident", False)),
        problems=problems,
    )


def inspect(image_bytes: bytes, api_key: str | None = None, model: str | None = None) -> Inspection:
    """Ask a vision model whether a rendered flyer looks right.

    Args:
        image_bytes: A PNG or JPEG of the rendered slide.
        api_key: OpenAI key. Defaults to the environment.
        model: Override the configured vision model.

    Returns:
        An `Inspection`. Never raises: this runs on the delivery path, and a
        failed check must leave the flyer deliverable-with-a-caveat rather than
        stopping everything.

    Raises:
        Nothing.
    """
    key = api_key or os.environ.get("OPENAI_IMAGE_API_KEY", "")
    if not key or not image_bytes:
        return Inspection(looks_right=False, confident=False, checked=False)

    encoded = base64.b64encode(image_bytes).decode()
    payload = {
        "model": model or os.environ.get("GABLE_VISION_MODEL", "gpt-5-mini"),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    },
                ],
            }
        ],
        "max_completion_tokens": 3000,
    }
    try:
        body = _post(payload, key)
    except Exception:
        logger.exception("the vision check could not run")
        return Inspection(looks_right=False, confident=False, checked=False)

    said = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return parse(said)
