"""Plan and verify one conversational change without touching a presentation.

The natural-language model may choose a supported edit, but it never chooses a
Slides object id.  This module resolves the spoken target against one complete
presentation snapshot, builds the narrow request through the existing pure
Slides tools, and later proves that only the intended leaf element changed.

Copying, network calls, visual inspection, and promotion of a verified draft
belong to the workflow around this module.  Keeping this layer pure makes the
most dangerous promise -- "exactly one element changed" -- independently
testable.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from gable.slides.edit_common import EditError, Request
from gable.slides.edits import (
    move_element,
    replace_text,
    scale_element,
    set_font_size,
    set_line_colour,
    set_text_colour,
)
from gable.slides.elements import font_size_pt, presentation_elements, text_content

_PHONE = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")
_PRICE = re.compile(r"\$\s?[\d,]+")


class EditPlanError(EditError):
    """A spoken edit could not be resolved to one safe Slides request."""


@dataclass(frozen=True, slots=True)
class PlannedEdit:
    """One exact mutation plus enough evidence to verify its readback."""

    tool: str
    target: str
    target_object_id: str
    requests: tuple[Request, ...]
    before_text: str
    after_text: str
    success_message: str
    dynamic_text: str = ""
    old_literal: str = ""
    new_literal: str = ""


def _normal(value: str) -> str:
    """Lowercase words with punctuation removed for semantic comparison."""
    return " ".join(
        "".join(character if character.isalnum() else " " for character in value.casefold()).split()
    )


def _number(arguments: Mapping[str, Any], name: str) -> float:
    """Read a numeric model argument without accepting booleans or blanks."""
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise EditPlanError(f"{name} is not a number")
    try:
        return float(value)
    except ValueError as exc:
        raise EditPlanError(f"{name} is not a number") from exc


def _elements(presentation: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return every leaf with a nonempty, unique object id."""
    elements = list(presentation_elements(presentation))
    identifiers = [str(element.get("objectId") or "") for _page, element in elements]
    if any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(
        identifiers
    ):
        raise EditPlanError("the flyer does not identify every editable element exactly once")
    return elements


def _text_candidates(
    target: str,
    elements: list[tuple[str, dict[str, Any]]],
    field_values: Mapping[str, Collection[str]],
) -> list[tuple[str, dict[str, Any]]]:
    """Resolve a spoken field name or literal against visible flyer text."""
    key = _normal(target)
    values = {_normal(value) for value in field_values.get(key, ()) if value.strip()}
    direct: list[tuple[str, dict[str, Any]]] = []
    for page_id, element in elements:
        text = text_content(element)
        if not text:
            continue
        text_key = _normal(text)
        if key and (key == text_key or key in text_key):
            direct.append((page_id, element))
            continue
        if any(value == text_key or value in text_key for value in values):
            direct.append((page_id, element))
    if direct:
        return direct
    if key in {"price", "list price", "sale price", "closing price"}:
        return [item for item in elements if _PRICE.fullmatch(text_content(item[1]))]
    if key in {"phone", "phone number"}:
        return [item for item in elements if _PHONE.fullmatch(text_content(item[1]))]
    return []


def _one(
    candidates: list[tuple[str, dict[str, Any]]],
    spoken_target: str,
) -> tuple[str, dict[str, Any]]:
    """Return exactly one candidate or stop rather than rank an ambiguity."""
    if len(candidates) != 1:
        target = spoken_target.strip() or "requested"
        raise EditPlanError(f"could not identify exactly one {target} element")
    return candidates[0]


def plan_edit(
    presentation: dict[str, Any],
    tool: str,
    arguments: Mapping[str, Any],
    field_values: Mapping[str, Collection[str]] | None = None,
) -> PlannedEdit:
    """Resolve one supported request against an immutable presentation snapshot.

    Args:
        presentation: Complete Slides ``presentations.get`` response.
        tool: One of the conversational edit tools exposed to the model.
        arguments: Validated model arguments; numeric and target guards are
            repeated here because the model is not a trust boundary.
        field_values: Current known values keyed by normalised spoken aliases.

    Returns:
        A request plan that names the one leaf allowed to change.

    Raises:
        EditPlanError: If the instruction is unsupported, malformed, missing a
            unique target, or would be a no-op visible only as false success.
    """
    elements = _elements(presentation)
    values = field_values or {}

    if tool == "set_font_size":
        target = str(arguments.get("target") or "").strip()
        points = _number(arguments, "points")
        _page_id, element = _one(_text_candidates(target, elements, values), target)
        current_pt = font_size_pt(element)
        if current_pt and abs(current_pt - points) < 0.001:
            raise EditPlanError(f"the {target or 'selected'} text is already that size")
        object_id = str(element["objectId"])
        return PlannedEdit(
            tool,
            target,
            object_id,
            tuple(set_font_size(object_id, points)),
            text_content(element),
            text_content(element),
            f"Done. I changed the {target or 'selected'} text to {points:g} points.",
            text_content(element),
        )

    if tool == "set_colour":
        target = str(arguments.get("target") or "").strip()
        colour = str(arguments.get("colour") or "").strip()
        candidates = _text_candidates(target, elements, values)
        if not candidates and _normal(target) in {"line", "divider", "middle line"}:
            candidates = [item for item in elements if "line" in item[1]]
        _page_id, element = _one(candidates, target)
        object_id = str(element["objectId"])
        requests = (
            set_line_colour(object_id, colour)
            if "line" in element
            else set_text_colour(object_id, colour)
        )
        return PlannedEdit(
            tool,
            target,
            object_id,
            tuple(requests),
            text_content(element),
            text_content(element),
            f"Done. I changed the {target or 'selected'} colour.",
        )

    if tool == "resize_photo":
        which = _normal(str(arguments.get("which") or ""))
        factor = _number(arguments, "factor")
        prefix = "gableHero_" if which == "hero" else "gableFace_" if which == "headshot" else ""
        if not prefix:
            raise EditPlanError("tell me whether the photo is the hero or the headshot")
        candidates = [
            item for item in elements if str(item[1].get("objectId") or "").startswith(prefix)
        ]
        _page_id, element = _one(candidates, f"{which} photo")
        object_id = str(element["objectId"])
        return PlannedEdit(
            tool,
            f"{which} photo",
            object_id,
            tuple(scale_element(object_id, factor, element.get("transform", {}))),
            "",
            "",
            f"Done. I resized the {which} photo.",
        )

    if tool == "move_element":
        target = str(arguments.get("target") or "").strip()
        key = _normal(target)
        if "hero" in key:
            candidates = [
                item
                for item in elements
                if str(item[1].get("objectId") or "").startswith("gableHero_")
            ]
        elif "headshot" in key:
            candidates = [
                item
                for item in elements
                if str(item[1].get("objectId") or "").startswith("gableFace_")
            ]
        else:
            candidates = _text_candidates(target, elements, values)
        _page_id, element = _one(candidates, target)
        object_id = str(element["objectId"])
        return PlannedEdit(
            tool,
            target,
            object_id,
            tuple(
                move_element(
                    object_id,
                    _number(arguments, "dx_points"),
                    _number(arguments, "dy_points"),
                )
            ),
            text_content(element),
            text_content(element),
            f"Done. I moved the {target or 'selected element'}.",
        )

    if tool == "correct_field":
        current_literal = str(arguments.get("current") or "")
        replacement = str(arguments.get("replacement") or "")
        matches: list[tuple[str, dict[str, Any]]] = []
        for page_id, element in elements:
            matches.extend([(page_id, element)] * text_content(element).count(current_literal))
        page_id, element = _one(matches, "current value")
        object_id = str(element["objectId"])
        before = text_content(element)
        after = before.replace(current_literal, replacement, 1)
        if before == after:
            raise EditPlanError("the requested field correction would not change the flyer")
        semantic_fields = [
            key
            for key, candidates in values.items()
            if any(value.strip() == current_literal for value in candidates)
        ]
        semantic_target = semantic_fields[0] if len(semantic_fields) == 1 else "field"
        return PlannedEdit(
            tool,
            semantic_target,
            object_id,
            tuple(replace_text(current_literal, replacement, [page_id], allow_short=True)),
            before,
            after,
            "Done. I corrected that field.",
            after,
            current_literal,
            replacement,
        )

    raise EditPlanError("that flyer change is not connected safely")


def batch_was_confirmed(plan: PlannedEdit, response: Mapping[str, Any]) -> bool:
    """Return whether Slides acknowledged every planned request exactly once."""
    replies = response.get("replies", [])
    if not isinstance(replies, list) or len(replies) != len(plan.requests):
        return False
    if plan.tool != "correct_field":
        return True
    changed = sum(
        int(reply.get("replaceAllText", {}).get("occurrencesChanged", 0))
        for reply in replies
        if isinstance(reply, dict)
    )
    return changed == 1


def presentation_changed_only_as_planned(
    before: dict[str, Any],
    after: dict[str, Any],
    plan: PlannedEdit,
) -> bool:
    """Prove the target changed and every other leaf stayed byte-equivalent."""
    try:
        before_items = {str(element["objectId"]): element for _page, element in _elements(before)}
        after_items = {str(element["objectId"]): element for _page, element in _elements(after)}
    except (EditPlanError, KeyError):
        return False
    if before_items.keys() != after_items.keys() or plan.target_object_id not in after_items:
        return False
    for object_id in before_items.keys() - {plan.target_object_id}:
        if _canonical(before_items[object_id]) != _canonical(after_items[object_id]):
            return False
    old_target = before_items[plan.target_object_id]
    new_target = after_items[plan.target_object_id]
    if _canonical(old_target) == _canonical(new_target):
        return False
    return (
        text_content(old_target) == plan.before_text and text_content(new_target) == plan.after_text
    )


def presentation_fingerprint(presentation: dict[str, Any]) -> str:
    """Hash stable visible structure for crash-safe source reconstruction.

    Copy-specific and expiring delivery metadata is excluded; every slide,
    element id, geometry, style, and text value remains in the proof.
    """
    visible = {
        "pageSize": presentation.get("pageSize"),
        "slides": presentation.get("slides"),
    }
    return hashlib.sha256(_canonical(visible).encode()).hexdigest()


def _canonical(value: dict[str, Any]) -> str:
    """Serialize a leaf without Google's expiring image delivery URL."""

    def stable(item: Any) -> Any:  # noqa: ANN401 - recursive JSON value
        if isinstance(item, dict):
            return {
                key: stable(child)
                for key, child in item.items()
                if key not in {"contentUrl", "thumbnailUrl"}
            }
        if isinstance(item, list):
            return [stable(child) for child in item]
        return item

    return json.dumps(stable(value), sort_keys=True, separators=(",", ":"))
