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
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Final

from gable.voice import safe

logger = logging.getLogger("gable.vision")

_ENDPOINT: Final[str] = "https://api.openai.com/v1/responses"
_TIMEOUT_SECONDS: Final[int] = 90
_DEFAULT_MODEL: Final[str] = "gpt-5.6-sol"
#: Reasoning tokens are drawn from this same budget, so it must cover the
#: model's private thinking *plus* the JSON verdict. Measured live on
#: 2026-08-13: a real Sold flyer spent 886 reasoning tokens at `effort: high`,
#: leaving ~114 of the previous 1000-token cap for the answer. The JSON
#: truncated mid-array, `parse` failed, and a correct build was reported as
#: "the visual inspection could not run". This is a ceiling, not a target;
#: a completed verdict costs far less.
_MAX_OUTPUT_TOKENS: Final[int] = 4000

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
For a very small supplied photo, the flyer may deliberately show one contained
copy at no more than 2x over a blurred and darkened fill made from the same
photo. Do not report that intentional backdrop as a blurry main property photo;
judge the contained foreground for identity, clarity, crop, and placement.

Choose exactly one remedy:
- "none" only when the flyer looks right.
- "replace_photo" only when the human-supplied property photo itself is
  provably the wrong image for this listing. The contradiction must be
  independently legible in the FIRST image, without borrowing any detail from
  the rendered flyer. Set "source_conflict_visible" true only in that case.
  If the first image is too small or blurry to read its house number, that is
  not source evidence, even if the SECOND image shows a conflicting number.
  A detail changed, invented, or visible only in the second image is an output
  problem and must use "review". Do not choose "replace_photo" for a crop,
  enlargement, enhancement drift, or another output problem that can be fixed
  while keeping the supplied photo.
- "review" for every other problem, any mixed set of problems, and any case
  where you are unsure which remedy applies.

For each problem, provide one matching kind in the same order:
- "source_photo_conflict" only for a contradiction independently legible in
  the first human-supplied image.
- "photo_output" for crop, placement, stretching, softness, or a change visible
  only in the rendered flyer.
- "text", "layout", "placeholder", or "other" for every non-photo problem.

Reply as JSON only:
{"looks_right": true|false, "confident": true|false, "problems": ["..."],
 "problem_kinds": ["source_photo_conflict"|"photo_output"|"text"|"layout"|
 "placeholder"|"other"],
 "remedy": "none"|"review"|"replace_photo", "source_conflict_visible": true|false}

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

Reply as JSON only. Use "none" when the template looks right and "review" for
every problem; a reusable template never uses "replace_photo" and always uses
false for "source_conflict_visible". Use "text", "layout", "placeholder", or
"other" as each matching problem kind; a template never has a
"source_photo_conflict" or "photo_output" problem:
{"looks_right": true|false, "confident": true|false, "problems": ["..."],
 "problem_kinds": ["text"|"layout"|"placeholder"|"other"],
 "remedy": "none"|"review", "source_conflict_visible": false}

Each problem must be one short sentence naming what and where, in plain words a
designer would use. No coordinates, no element ids, no technical terms.
"""

_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "looks_right": {"type": "boolean"},
        "confident": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
        "problem_kinds": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "source_photo_conflict",
                    "photo_output",
                    "text",
                    "layout",
                    "placeholder",
                    "other",
                ],
            },
        },
        "remedy": {
            "type": "string",
            "enum": ["none", "review", "replace_photo"],
        },
        "source_conflict_visible": {"type": "boolean"},
    },
    "required": [
        "looks_right",
        "confident",
        "problems",
        "problem_kinds",
        "remedy",
        "source_conflict_visible",
    ],
    "additionalProperties": False,
}

_TEMPLATE_SCHEMA: Final[dict[str, Any]] = {
    **_SCHEMA,
    "properties": {
        **_SCHEMA["properties"],
        "problem_kinds": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["text", "layout", "placeholder", "other"],
            },
        },
        "remedy": {"type": "string", "enum": ["none", "review"]},
        "source_conflict_visible": {"type": "boolean", "enum": [False]},
    },
}


class InspectionRemedy(StrEnum):
    """The next safe runtime state after a confident visual verdict."""

    NONE = "none"
    REVIEW = "review"
    REPLACE_PHOTO = "replace_photo"


class InspectionProblemKind(StrEnum):
    """A typed visual problem used to enforce safe recovery in code."""

    SOURCE_PHOTO_CONFLICT = "source_photo_conflict"
    PHOTO_OUTPUT = "photo_output"
    TEXT = "text"
    LAYOUT = "layout"
    PLACEHOLDER = "placeholder"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Inspection:
    """What the vision pass concluded."""

    looks_right: bool
    confident: bool
    problems: list[str] = field(default_factory=list)
    #: True when the check itself could not run. Distinct from "it looks wrong",
    #: because the honest response to each is different.
    checked: bool = True
    #: Defaults to human review for injected/legacy negative verdicts. Only an
    #: explicit, strict-schema REPLACE_PHOTO result may turn a failed render
    #: directly back into the photo-upload state.
    remedy: InspectionRemedy = InspectionRemedy.REVIEW
    #: One typed category per problem. A missing or mixed category list can
    #: never authorize replacing a human-supplied source.
    problem_kinds: tuple[InspectionProblemKind, ...] = ()
    #: True only when the contradiction is independently visible in the first,
    #: human-supplied image. A detail found only in the rendered derivative is
    #: never evidence against the source upload.
    source_conflict_visible: bool = False

    @property
    def needs_source_replacement(self) -> bool:
        """Whether this verdict safely authorizes asking for another upload."""
        return (
            self.checked
            and self.confident
            and not self.looks_right
            and self.remedy is InspectionRemedy.REPLACE_PHOTO
            and self.source_conflict_visible
            and bool(self.problems)
            and len(self.problem_kinds) == len(self.problems)
            and all(
                kind is InspectionProblemKind.SOURCE_PHOTO_CONFLICT for kind in self.problem_kinds
            )
        )

    def without_expected_placeholders(self) -> Inspection:
        """Drop only the problems naming a placeholder Gable left on purpose.

        A value nobody supplied was already asked for once, and Chase's rule is
        that the flyer still ships with the design's own placeholder showing.
        The visual gate would otherwise report exactly that as a defect and
        park a correct flyer in review.

        Returns:
            A verdict with placeholder-kind problems removed, passing when
            nothing else remained. Unchanged when the categories do not line up
            one-to-one with the problems, because then nothing can be dropped
            safely and the flyer must still go to a person.

        Raises:
            Nothing.
        """
        if len(self.problem_kinds) != len(self.problems) or not self.problems:
            return self
        kept = [
            (problem, kind)
            for problem, kind in zip(self.problems, self.problem_kinds, strict=True)
            if kind is not InspectionProblemKind.PLACEHOLDER
        ]
        if len(kept) == len(self.problems):
            return self
        problems = [problem for problem, _ in kept]
        return replace(
            self,
            problems=problems,
            problem_kinds=tuple(kind for _, kind in kept),
            looks_right=self.looks_right or not problems,
            remedy=self.remedy if problems else InspectionRemedy.NONE,
        )

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


def parse(reply: str, *, has_reference_photo: bool = False) -> Inspection:
    """Read the model's answer, tolerating the ways models wrap JSON.

    Args:
        reply: The model's raw text.
        has_reference_photo: Whether the request actually included the first,
            human-supplied image needed to prove a source contradiction.

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
    raw_problem_kinds = data.get("problem_kinds")
    raw_remedy = data.get("remedy")
    source_conflict_visible = data.get("source_conflict_visible")
    if (
        not isinstance(looks_right, bool)
        or not isinstance(confident, bool)
        or not isinstance(raw_problems, list)
        or any(not isinstance(problem, str) for problem in raw_problems)
        or not isinstance(raw_problem_kinds, list)
        or any(not isinstance(kind, str) for kind in raw_problem_kinds)
        or len(raw_problem_kinds) != len(raw_problems)
        or not isinstance(raw_remedy, str)
        or not isinstance(source_conflict_visible, bool)
    ):
        return Inspection(looks_right=False, confident=False, checked=False)
    try:
        remedy = InspectionRemedy(raw_remedy)
        problem_kinds = tuple(InspectionProblemKind(kind) for kind in raw_problem_kinds)
    except ValueError:
        return Inspection(looks_right=False, confident=False, checked=False)
    problems = [problem.strip() for problem in raw_problems]
    if any(not problem for problem in problems):
        return Inspection(looks_right=False, confident=False, checked=False)
    if looks_right and problems:
        # Preserve a named defect, but an internally contradictory "pass" can
        # never carry enough certainty to blame and replace the human source.
        looks_right = False
        remedy = InspectionRemedy.REVIEW
        source_conflict_visible = False
    if looks_right:
        if remedy is not InspectionRemedy.NONE or source_conflict_visible:
            return Inspection(looks_right=False, confident=False, checked=False)
    elif remedy is InspectionRemedy.NONE or not problems:
        return Inspection(looks_right=False, confident=False, checked=False)
    if remedy is InspectionRemedy.REPLACE_PHOTO and (not confident or not problems):
        return Inspection(looks_right=False, confident=False, checked=False)
    if remedy is InspectionRemedy.REPLACE_PHOTO and (
        not source_conflict_visible
        or not has_reference_photo
        or any(kind is not InspectionProblemKind.SOURCE_PHOTO_CONFLICT for kind in problem_kinds)
    ):
        # Preserve the visible problem but refuse to blame the human source
        # unless the first image was present and independently proved it.
        remedy = InspectionRemedy.REVIEW
        source_conflict_visible = False
    elif remedy is not InspectionRemedy.REPLACE_PHOTO and source_conflict_visible:
        # A source-evidence flag without the matching typed remedy is internally
        # inconsistent. Preserve the visible problem as review, never as blame
        # for the human upload.
        source_conflict_visible = False
    return Inspection(
        looks_right=looks_right,
        confident=confident,
        problems=problems,
        remedy=remedy,
        problem_kinds=problem_kinds,
        source_conflict_visible=source_conflict_visible,
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
    schema: dict[str, Any] | None = None,
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
                "schema": schema or _SCHEMA,
                "strict": True,
            }
        },
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
    }
    try:
        body = _post(payload, key)
    except Exception:
        logger.exception("the vision check could not run")
        return Inspection(looks_right=False, confident=False, checked=False)

    # A verdict cut off by the token ceiling parses as unreadable, which is
    # indistinguishable from a refusal or a malformed reply in the run's
    # "could not run" outcome. Name it here so the next occurrence is one log
    # line rather than another live reproduction.
    _warn_if_truncated(body)
    return parse(_output_text(body), has_reference_photo=bool(reference_image_bytes))


def _warn_if_truncated(body: dict[str, Any]) -> None:
    """Log when a reply exhausted the output budget instead of finishing.

    Args:
        body: One decoded Responses payload.

    Raises:
        Nothing. Diagnosis must never change the verdict.
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return
    spent = usage.get("output_tokens")
    if not isinstance(spent, int) or spent < _MAX_OUTPUT_TOKENS:
        return
    details = usage.get("output_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    logger.error(
        "the visual inspection exhausted its %d-token output budget "
        "(%s reasoning tokens), so its verdict was cut off",
        _MAX_OUTPUT_TOKENS,
        reasoning if reasoning is not None else "unknown",
    )


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
            the same call, so a bad crop or placement cannot pass as good layout.

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
    return _inspect(
        image_bytes,
        TEMPLATE_PROMPT,
        api_key=api_key,
        model=model,
        schema=_TEMPLATE_SCHEMA,
    )
