"""Execute conversational edits against the flyer in a Slack thread.

The model may choose a capability, but it never chooses a Slides object id or
builds an API request. This module resolves the thread to a database run,
resolves Carmen's words to exactly one element, asks rather than guessing when
that is ambiguous, uses the pure builders in ``slides.edits``, and reports
success only after Google returns a complete batch reply.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any

from gable.db import store
from gable.sheets import repository as sheet_repo
from gable.slackapp.brain import Decision
from gable.slides.edit_common import EditError
from gable.slides.edits import (
    move_element,
    occurrences_changed,
    replace_text,
    scale_element,
    set_font_size,
    set_line_colour,
    set_shape_fill,
    set_text_colour,
)
from gable.slides.elements import presentation_elements, text_content

logger = logging.getLogger("gable.slack.editing")

_PHONE = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")
_PRICE = re.compile(r"\$\s?[\d,]+")


def _normal(value: str) -> str:
    """Lowercase words with punctuation removed for semantic comparison."""
    return " ".join("".join(char if char.isalnum() else " " for char in value.lower()).split())


def _context_values(connection: Connection, run: store.RunRow, target: str) -> list[str]:
    """Resolve a spoken field name to the actual value rendered for this run."""
    stored = store.load_submission(connection, run.response_row_id)
    if stored is None:
        return []
    intake = stored.intake
    person = sheet_repo.find_salesperson(connection, intake.agent_email)
    person_name = " ".join(
        part for part in (person.get("first_name", ""), person.get("last_name", "")) if part
    )
    key = _normal(target)
    mapping: tuple[tuple[tuple[str, ...], str], ...] = (
        (
            ("price", "list price", "sale price", "closing price"),
            intake.price,
        ),
        (("address", "property address"), intake.address),
        (("agent", "agent name", "name"), person_name or intake.agent_name),
        (("phone", "phone number"), person.get("phone", "")),
        (("email", "email address"), intake.agent_email),
        (("open house", "open house time", "open house date"), intake.open_house),
    )
    values: list[str] = []
    for aliases, value in mapping:
        if key in aliases and value.strip():
            values.append(value.strip())
    return values


def _text_candidates(
    connection: Connection,
    run: store.RunRow,
    target: str,
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find text shapes matching a literal or a field's rendered value."""
    target_key = _normal(target)
    values = [_normal(value) for value in _context_values(connection, run, target)]
    direct: list[dict[str, Any]] = []
    for element in elements:
        text = text_content(element)
        if not text:
            continue
        text_key = _normal(text)
        if target_key and (target_key == text_key or target_key in text_key):
            direct.append(element)
            continue
        if any(value and (value == text_key or value in text_key) for value in values):
            direct.append(element)
    if direct:
        return direct
    if target_key in {"price", "list price", "sale price", "closing price"}:
        return [element for element in elements if _PRICE.fullmatch(text_content(element))]
    if target_key in {"phone", "phone number"}:
        return [element for element in elements if _PHONE.fullmatch(text_content(element))]
    return []


def _one(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the only candidate; ambiguity is a refusal, not a ranking."""
    return candidates[0] if len(candidates) == 1 else None


def _number(arguments: dict[str, Any], name: str) -> float:
    """Read a numeric model argument without allowing missing or boolean values."""
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        msg = f"{name} is not a number"
        raise ValueError(msg)
    return float(value)


@dataclass(slots=True)
class SlideEditor:
    """Apply one model-selected edit to the output file in a Slack thread."""

    connection: Connection
    slides: Any

    def execute(self, decision: Decision, thread_ts: str) -> str:
        """Execute a supported decision and return its verified outcome.

        Args:
            decision: Model-selected capability and validated argument shape.
            thread_ts: Slack thread that identifies the run and output file.

        Returns:
            Plain words safe for the Slack style gate.

        Raises:
            Nothing. Ambiguity and API failure both preserve the current flyer.
        """
        run = store.run_for_thread(self.connection, thread_ts)
        if run is None:
            return "I could not match this thread to a listing, so I have not changed anything."
        if decision.tool == "report_status":
            return self._status(run)
        if store.has_pending_run_notification(self.connection, run.run_id):
            return (
                "I am still confirming the last outcome in this thread, so I did not "
                "change the flyer again."
            )
        if not run.output_file_id:
            return "This listing does not have a built flyer yet, so there is nothing to edit."
        if decision.tool != "replace_photo":
            # The delivery message offers "send them here and I will run it
            # again" for missing values, and a reply that took that offer was
            # answered with this refusal — the promise and the guard
            # contradicted each other. Listing values route to the rebuild
            # path at the brain now; this branch remains for true in-place
            # edits, and its words must not contradict that offer.
            return (
                "I do not edit a delivered flyer in place. Say the values that "
                "should change — like 4 beds, 2 baths, $500,000 — and I will "
                "rebuild it with them."
            )
        if decision.tool == "replace_photo":
            return self._begin_photo_replacement(run, decision.arguments)

        try:
            presentation = (
                self.slides.presentations().get(presentationId=run.output_file_id).execute()
            )
            elements = [element for _page_id, element in presentation_elements(presentation)]
            if decision.tool == "set_font_size":
                return self._font_size(run, elements, decision.arguments)
            if decision.tool == "set_colour":
                return self._colour(run, elements, decision.arguments)
            if decision.tool == "resize_photo":
                return self._resize_photo(run, elements, decision.arguments)
            if decision.tool == "move_element":
                return self._move(run, elements, decision.arguments)
            if decision.tool == "correct_field":
                return self._correct_field(run, presentation, decision.arguments)
            if decision.tool == "rebuild_flyer":
                return (
                    "I understood that you want a rebuild, but I could not safely start one "
                    "from this thread. I have not changed the flyer."
                )
            return "I understood the request, but I do not have a safe edit for it yet."
        except (EditError, TypeError, ValueError):
            logger.exception("a conversational edit was rejected before reaching Slides")
            return "I could not apply that change safely, so I have not changed the flyer."
        except Exception:
            logger.exception("a conversational Slides edit failed")
            return "Google Slides did not accept that change, so I left the flyer unchanged."

    def _begin_photo_replacement(
        self,
        run: store.RunRow,
        arguments: dict[str, Any],
    ) -> str:
        """Pause a built run for one explicitly targeted replacement image.

        Args:
            run: Existing thread-owned flyer run.
            arguments: Model arguments containing the confirmed image target.

        Returns:
            The exact source Carmen or Chase must update next.

        Raises:
            Nothing. Unsupported targets and states leave the current flyer
            and run untouched.
        """
        which = _normal(str(arguments.get("which") or ""))
        if which not in {"hero", "headshot"}:
            return "Tell me whether you mean the hero photo or the headshot before I replace it."
        if run.status not in {"delivered", "needs_review"}:
            # "Waiting on something else" is false when the thing it is waiting
            # on IS the photograph, which is exactly the state someone asks to
            # replace one from. Naming the upload is also the whole remedy: the
            # ordinary file handoff takes a photo in any paused state that
            # asked for one, so there is nothing here for this tool to do.
            if which == "hero" and (run.status == "needs_photo" or run.awaiting_photo):
                return "Send me the property photo here and I will build with it."
            return (
                "This listing is already waiting on something else, so I left its current "
                "flyer and status unchanged."
            )

        try:
            if which == "hero":
                # Keep output_file_id, output_url and the prior photo URL in
                # place. The linked flyer remains untouched while a new Slack
                # upload is rebuilt through the normal geometry/readback/vision
                # gates; PhotoHandoff atomically replaces photo provenance only
                # when it claims this pause.
                store.set_status(
                    self.connection,
                    run.run_id,
                    "needs_photo",
                    "waiting for a replacement property photo",
                    failure_reason="Send me the new property photo.",
                )
                return "Send me the new property photo."

            stored = store.load_submission(self.connection, run.response_row_id)
            agent = stored.intake.agent_name if stored is not None else "the agent"
            instruction = (
                f"Replace {agent}'s image in Head Shots, then tell me to rebuild the flyer."
            )
            # Agent portraits remain human-owned Drive data. A Slack upload in
            # this state is not accepted as an ad-hoc headshot: the ordinary
            # file handoff accepts only needs_photo, while this needs_info pause
            # resumes after the authoritative folder has been updated.
            store.set_status(
                self.connection,
                run.run_id,
                "needs_info",
                "waiting for an updated filed agent headshot",
                failure_reason=instruction,
            )
            return instruction
        except Exception:
            logger.exception("could not record a conversational photo replacement")
            return (
                "I could not start the photo replacement, so I left the current flyer "
                "and its status unchanged."
            )

    def _font_size(
        self,
        run: store.RunRow,
        elements: list[dict[str, Any]],
        arguments: dict[str, Any],
    ) -> str:
        """Set one uniquely resolved text element's size."""
        target = str(arguments.get("target") or "").strip()
        points = _number(arguments, "points")
        element = _one(_text_candidates(self.connection, run, target, elements))
        if element is None:
            return self._ambiguous(target)
        requests = set_font_size(str(element.get("objectId") or ""), points)
        if not self._apply(run, requests, f"changed the {target or 'selected'} text size"):
            return "Google Slides did not confirm the text change, so I left it unreported."
        return f"Done. I changed the {target or 'selected'} text to {points:g} points."

    def _colour(
        self,
        run: store.RunRow,
        elements: list[dict[str, Any]],
        arguments: dict[str, Any],
    ) -> str:
        """Recolour exactly one text shape, line, or untextured shape."""
        target = str(arguments.get("target") or "").strip()
        colour = str(arguments.get("colour") or "").strip()
        candidates = _text_candidates(self.connection, run, target, elements)
        if not candidates and _normal(target) in {"line", "divider", "middle line"}:
            candidates = [element for element in elements if "line" in element]
        element = _one(candidates)
        if element is None:
            return self._ambiguous(target)
        object_id = str(element.get("objectId") or "")
        if "line" in element:
            requests = set_line_colour(object_id, colour)
        elif text_content(element):
            requests = set_text_colour(object_id, colour)
        else:
            requests = set_shape_fill(object_id, colour)
        if not self._apply(run, requests, f"changed the {target or 'selected'} colour"):
            return "Google Slides did not confirm the colour change, so I left it unreported."
        return f"Done. I changed the {target or 'selected'} colour."

    def _resize_photo(
        self,
        run: store.RunRow,
        elements: list[dict[str, Any]],
        arguments: dict[str, Any],
    ) -> str:
        """Resize a uniquely identified hero or headshot without moving it."""
        which = str(arguments.get("which") or "").strip().lower()
        factor = _number(arguments, "factor")
        images = [element for element in elements if "image" in element]
        heroes = [
            element
            for element in images
            if str(element.get("objectId") or "").startswith("gableHero")
        ]
        if which == "hero":
            candidates = heroes or (images if len(images) == 1 else [])
        elif which == "headshot":
            candidates = [element for element in images if element not in heroes]
        else:
            return "Tell me whether you mean the hero photo or the headshot before I resize it."
        element = _one(candidates)
        if element is None:
            return self._ambiguous(f"{which} photo")
        requests = scale_element(
            str(element.get("objectId") or ""),
            factor,
            element.get("transform", {}),
        )
        if not self._apply(run, requests, f"resized the {which} photo"):
            return "Google Slides did not confirm the photo change, so I left it unreported."
        return f"Done. I resized the {which} photo."

    def _correct_field(
        self,
        run: store.RunRow,
        presentation: dict[str, Any],
        arguments: dict[str, Any],
    ) -> str:
        """Replace a literal only when it occurs exactly once."""
        current = str(arguments.get("current") or "")
        replacement = str(arguments.get("replacement") or "")
        matches: list[str] = []
        for page_id, element in presentation_elements(presentation):
            matches.extend([page_id] * text_content(element).count(current))
        if len(matches) != 1:
            return self._ambiguous("current value")
        requests = replace_text(current, replacement, [matches[0]], allow_short=True)
        response = self._batch(run.output_file_id, requests)
        replies = response.get("replies", [])
        changed = sum(occurrences_changed(reply) for reply in replies)
        if changed != 1:
            return "Google Slides did not confirm one field change, so I left it unreported."
        store.set_status(self.connection, run.run_id, run.status, "corrected one flyer field")
        return "Done. I corrected that field."

    def _move(
        self,
        run: store.RunRow,
        elements: list[dict[str, Any]],
        arguments: dict[str, Any],
    ) -> str:
        """Nudge one uniquely resolved photo or text element."""
        target = str(arguments.get("target") or "").strip()
        target_key = _normal(target)
        dx_points = _number(arguments, "dx_points")
        dy_points = _number(arguments, "dy_points")
        images = [element for element in elements if "image" in element]
        heroes = [
            element
            for element in images
            if str(element.get("objectId") or "").startswith("gableHero")
        ]
        if "hero" in target_key:
            candidates = heroes or (images if len(images) == 1 else [])
        elif "headshot" in target_key:
            candidates = [element for element in images if element not in heroes]
        else:
            candidates = _text_candidates(self.connection, run, target, elements)
        element = _one(candidates)
        if element is None:
            return self._ambiguous(target)
        requests = move_element(
            str(element.get("objectId") or ""),
            dx_points,
            dy_points,
        )
        if not self._apply(run, requests, f"moved the {target or 'selected element'}"):
            return "Google Slides did not confirm the move, so I left it unreported."
        return f"Done. I moved the {target or 'selected element'}."

    def _apply(
        self,
        run: store.RunRow,
        requests: list[dict[str, Any]],
        detail: str,
    ) -> bool:
        """Apply a request list and audit it only after a complete reply."""
        response = self._batch(run.output_file_id, requests)
        if len(response.get("replies", [])) != len(requests):
            return False
        store.set_status(self.connection, run.run_id, run.status, detail)
        return bool(requests)

    def _batch(self, file_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Send one Slides batch and return its response."""
        response: dict[str, Any] = (
            self.slides.presentations()
            .batchUpdate(presentationId=file_id, body={"requests": requests})
            .execute()
        )
        return response

    @staticmethod
    def _ambiguous(target: str) -> str:
        """Explain that resolving the target was unsafe."""
        spoken = target or "requested"
        return (
            f"I could not identify exactly one {spoken} element, so I have not changed "
            "the flyer. Tell me the exact text you mean."
        )

    @staticmethod
    def _status(run: store.RunRow) -> str:
        """Describe the run state without exposing its internal identifier."""
        if run.status == "delivered":
            return "This flyer is built and linked in this thread."
        if run.status == "needs_photo":
            return "This listing is waiting for its hero photo."
        if run.status == "needs_review":
            return "This flyer is paused because its checks did not prove it is ready."
        if run.status in {"needs_info", "needs_template"}:
            # One status names the work only a person can do; it does not name
            # everything outstanding. Answering "what is it waiting for" with
            # the blocker alone hid a photo request made in the same message.
            if run.awaiting_photo:
                return (
                    "This listing is paused while it waits for the missing detail, and it is "
                    "still waiting for the property photo."
                )
            return "This listing is paused while it waits for the missing detail."
        if run.status in {"pending", "building"}:
            return "This listing is still being worked on."
        if run.status == "failed":
            return "This listing stopped because processing failed. I did not send it as finished."
        if run.status == "skipped":
            return "This request was skipped, so I did not build a flyer for it."
        return "I could not determine this listing's current state, so I will not guess."
