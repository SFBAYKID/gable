"""Build one test flyer from a design, measure it, and throw the copy away.

Brittney Bushee's Under Contract flyer on 2026-09-01 was refused over a
headshot well the design itself drew past the page edge. The scheduled scan
had read that design after Carmen's edit and found "no structural or
text-capacity problem", because the scan measures fields and frames and never
builds anything. The defect only showed on a real listing, in Carmen's thread,
with a real agent waiting.

So a design that has just been added or edited is now built once with sample
values, a sample photograph and a sample face, put through the same fill
readback, text fitting and geometric audit as a listing, and the copy is moved
to the trash. What that build shows is reported in the design's own thread —
before any listing arrives. A clean build says nothing, by the 2026-08-19 rule
that a quiet re-read is not news.

Pure orchestration: every read, write and placement is injected, so the whole
path runs in a test with no Google client. Costs Drive writes and no model
spend; the visual pass is not run here. Does not handle: deciding WHEN to
build, which is `template_triage`'s call.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from gable.pipeline import fill_check, run_images, run_reporting, run_values
from gable.slides import fields as template_fields
from gable.slides import manifest as template_manifest
from gable.voice import paragraphs, safe

logger = logging.getLogger("gable.canary")

#: The values a test flyer is built with. They are obviously samples on the
#: page and long enough to exercise the boxes a real listing would.
SAMPLE_VALUES: Final[dict[str, str]] = {
    "address": "12345 Sample Property Lane, Cockeysville, MD 21030",
    "price": "$1,249,000",
    "list_price": "$1,249,000",
    "closing_price": "$1,249,000",
    "new_price": "$1,199,000",
    "beds": "4",
    "baths": "3.5",
    "square_feet": "2,850",
    "agent_name": "Samantha Sampleworth",
    "agent_first_name": "Samantha",
    "agent_last_name": "Sampleworth",
    "agent_phone": "410.555.0123",
    "agent_email": "samantha@cornerhouserealty.com",
    "agent_title": "Realtor",
    "website": "cornerhouserealty.com",
    "open_house": "Sunday, Sep. 14 1-3PM",
    "open_house_date": "Sunday, Sep. 14",
    "open_house_time": "1-3PM",
    "review_quote": (
        "Samantha guided us through every step with patience and real expertise. "
        "We could not have asked for a better experience."
    ),
    "client_name": "The Sample Family",
    "neighborhood": "Cockeysville",
    "listing_note": "Sample listing note for a test build.",
}

#: What the Slides copy is called while it exists. Named so a person finding
#: one in the Gable drive knows it was meant to be trashed.
COPY_NAME_PREFIX: Final[str] = "Gable test build —"


@dataclass(frozen=True, slots=True)
class Canary:
    """What one test build showed, in Carmen's words and for the audit trail."""

    notes: tuple[str, ...] = ()
    detail: str = ""

    @property
    def clean(self) -> bool:
        """Whether the test flyer came out the way the design promised."""
        return not self.notes


@dataclass(frozen=True, slots=True)
class Seams:
    """Every outside call a test build makes, injected."""

    read_slide_text: Callable[[str], list[str]]
    read_presentation: Callable[[str], dict[str, Any]]
    read_text_boxes: Callable[[str], list[Any]]
    copy_template: Callable[[str, str], tuple[str, str]]
    fill: Callable[[str, dict[str, str]], int]
    apply: Callable[[str, list[dict[str, Any]]], None]
    place_photo: Callable[[str, str, str, str], bool]
    place_headshot: Callable[[str, str, dict[str, str], str], bool | None]
    trash: Callable[[str], None]
    hero_url: str
    face_url: str


def sample_values(resolution: template_fields.Resolution, face_url: str) -> dict[str, str]:
    """A value for every field the design resolves, plus the sample face.

    Args:
        resolution: What the design's text was understood to mean.
        face_url: The published sample headshot.

    Returns:
        Field name to value. A field this module has no sample for gets its
        own name written out, so the box is still filled and measured.

    Raises:
        Nothing.
    """
    values = {
        name: SAMPLE_VALUES.get(name, f"Sample {name.replace('_', ' ')}")
        for name in resolution.fields
    }
    values["headshot"] = face_url
    return values


def dry_build(name: str, file_id: str, seams: Seams) -> Canary:
    """Build a test flyer from one design and say what it showed.

    Args:
        name: The design's name, as filed.
        file_id: The design's Drive id.
        seams: Every outside call.

    Returns:
        A `Canary`. Empty notes mean the design built cleanly.

    Raises:
        Nothing. A build that could not run reports that as its note, and the
        copy is trashed whatever happened after it was made.
    """
    resolution = template_fields.resolve(seams.read_slide_text(file_id))
    values = sample_values(resolution, seams.face_url)
    pairs = template_fields.replacements(resolution, values)
    carries_a_photo = template_manifest.manifest_for(name).find("hero_photo") is not None
    output_id = ""
    notes: list[str] = []
    details: list[str] = []
    try:
        output_id, _url = seams.copy_template(file_id, f"{COPY_NAME_PREFIX} {name}")
        changed = seams.fill(output_id, pairs)
        readback = run_reporting.read_back(seams.read_slide_text, output_id)
        verdict = fill_check.check_fill(
            pairs, changed, readback, values, resolution, run_values.OFFICE_PHONE
        )
        if verdict.stop is not None:
            notes.append(verdict.stop.spoken.split("\n\n", 1)[0])
            details.append(verdict.stop.detail)
        notes.extend(verdict.notes)
        details.extend(verdict.details)
        unplaced = run_images.place_all(
            f"canary-{file_id}",
            output_id,
            name,
            seams.hero_url,
            values,
            carries_a_photo=carries_a_photo,
            place_photo=seams.place_photo,
            place_headshot=seams.place_headshot,
        )
        if unplaced:
            notes.append(run_images.delivery_note(unplaced, values.get("agent_name", "")))
            details.append(f"it {unplaced}")
        text_fit = run_reporting.fit_changed_text(
            seams.read_text_boxes, seams.apply, output_id, pairs, resolution, values
        )
        if text_fit.unreadable:
            words = " ".join(text_fit.unreadable[0].text.split())[:24]
            notes.append(f"The {words} would have to be shrunk so far it would be hard to read.")
            details.append(f"unreadable fit: {words}")
        moved = run_reporting.layout_notice(
            seams.read_presentation(file_id), seams.read_presentation(output_id)
        )
        if moved is not None:
            notes.append(moved.spoken)
            details.append(moved.detail)
    except Exception:
        logger.exception("the test build of %s could not run", name)
        notes.append("I could not finish building a test flyer from it.")
        details.append("the test build raised")
    finally:
        if output_id:
            try:
                seams.trash(output_id)
            except Exception:
                logger.exception("the test copy of %s could not be trashed", name)
                notes.append(
                    f"I could not move the test copy to the trash; it is in the Gable "
                    f"drive as {COPY_NAME_PREFIX} {name}."
                )
    return Canary(tuple(safe(note) for note in notes), "; ".join(details)[:400])


def report(name: str, canary: Canary) -> str:
    """The paragraph for the design's thread, or "" when the build was clean.

    Args:
        name: The design's name.
        canary: What the build showed.

    Returns:
        A lead sentence and one sentence per finding, or "".

    Raises:
        Nothing.
    """
    if canary.clean:
        return ""
    lead = f"I also built a test flyer from the {name} design with sample values."
    return safe(paragraphs(lead, *canary.notes))
