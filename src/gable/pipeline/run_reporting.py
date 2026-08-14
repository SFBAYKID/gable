"""Small reporting helpers shared by the flyer runner's outcome branches."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any, Final

from gable.db import store
from gable.pipeline.orchestrator import QualityVerdict
from gable.pipeline.vision import Inspection
from gable.slides import fields as template_fields
from gable.slides import fitting, layout, preflight
from gable.voice import safe

logger = logging.getLogger("gable.runner")


@dataclass(frozen=True, slots=True)
class TextFitResult:
    """Actual Slides text-size changes and any unreadable fit."""

    count: int = 0
    unreadable: tuple[fitting.Fit, ...] = ()
    note: str = ""


def fit_changed_text(
    read_text_boxes: Callable[[str], list[fitting.TextBox]],
    apply: Callable[[str, list[dict[str, Any]]], None],
    output_id: str,
    pairs: Mapping[str, str],
    resolution: template_fields.Resolution,
    values: Mapping[str, str],
) -> TextFitResult:
    """Resize filled text boxes and describe only changes Slides received."""
    # Keyed on what was WRITTEN, not the value before the design's own
    # conventions were applied. A supplied "4 beds" is written as the design's
    # "4 BEDS", and matching on the raw value silently dropped that box out of
    # the single-line set — the same mismatch the read-back check already hit.
    single_line = {
        written
        for name in preflight.SINGLE_LINE_FIELDS
        if (literal := resolution.fields.get(name))
        and values.get(name, "").strip()
        and (written := pairs.get(literal, "").strip())
    }
    fits = fitting.plan_fits(
        read_text_boxes(output_id),
        dynamic=pairs.values(),
        single_line=single_line,
    )
    shrunk = [fit for fit in fits if fit.overflows]
    unreadable = tuple(fit for fit in shrunk if fit.too_small_to_read)
    applied = [fit for fit in shrunk if not fit.too_small_to_read]
    if applied:
        apply(output_id, fitting.requests_for(applied))

    field_by_value: dict[str, list[str]] = {}
    for name in resolution.fields:
        value = values.get(name, "").strip()
        if value:
            field_by_value.setdefault(value, []).append(name.replace("_", " "))
    adjusted: list[str] = []
    for fit in applied:
        for name in field_by_value.get(fit.text.strip(), []):
            if name not in adjusted:
                adjusted.append(name)
    if not applied:
        note = ""
    elif not adjusted:
        note = "I reduced the filled text sizes to keep them inside their template boxes."
    else:
        names = (
            adjusted[0] if len(adjusted) == 1 else f"{', '.join(adjusted[:-1])} and {adjusted[-1]}"
        )
        noun = "size" if len(adjusted) == 1 else "sizes"
        pronoun = "it" if len(adjusted) == 1 else "them"
        note = f"I reduced the {names} text {noun} to keep {pronoun} inside the template boxes."
    return TextFitResult(len(applied), unreadable, note)


def read_back(read_slide_text: Callable[[str], list[str]], file_id: str) -> str | None:
    """Return all rendered flyer text, or ``None`` when verification failed."""
    try:
        return "\n".join(read_slide_text(file_id))
    except Exception:
        logger.exception("could not read the flyer back for verification")
        return None


def photo_note(connection: Connection, run_id: str) -> str:
    """Describe only the photo processing that actually happened."""
    row = connection.execute(
        "SELECT photo_source, ai_enhanced FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row or row["photo_source"] not in {"carmen", "slack_upload"}:
        return ""
    if int(row["ai_enhanced"] or 0):
        return "I sharpened, enlarged, and fitted the photo and finished the flyer."
    return "I resized and fitted the photo and finished the flyer."


@dataclass(frozen=True, slots=True)
class Verified:
    """What checking one rendered flyer twice concluded.

    The text pass proves every value is PRESENT; only the visual pass can see
    whether it FITS, and the gap between those two questions delivered a
    clipped flyer once already.
    """

    #: Everything wrong with the render, text problems first. Empty means it
    #: is safe to send as finished.
    problems: tuple[str, ...] = ()
    #: What Gable says when there is a problem. Empty when there is none.
    spoken: str = ""
    #: True only when a replacement property photo is the whole remedy.
    needs_replacement_photo: bool = False

    @property
    def ok(self) -> bool:
        """Whether the flyer passed both checks."""
        return not self.problems

    @property
    def detail(self) -> str:
        """One bounded audit line naming every problem."""
        return "; ".join(self.problems)[:400]


def verify_rendered(
    run_id: str,
    output_id: str,
    *,
    read_slide_text: Callable[[str], list[str]],
    thumbnail: Callable[[str], bytes],
    look_at: Callable[[str, bytes, tuple[str, ...]], Inspection],
    judge_text: Callable[[str, dict[str, str], int, tuple[str, ...]], QualityVerdict],
    pairs: Mapping[str, str],
    resolution: template_fields.Resolution,
    values: Mapping[str, str],
    text_fit: TextFitResult,
) -> Verified:
    """Read the rendered flyer back, look at it, and decide whether it passed.

    Args:
        run_id: The run being verified, for the visual inspector's ledger.
        output_id: The rendered Slides file.
        read_slide_text: Reads every text run out of a file.
        thumbnail: Renders the file to image bytes.
        look_at: The visual inspection, given the run and the rendered image.
        judge_text: The text verdict, given the read-back text and what was
            expected.
        pairs: The literal-to-value substitutions this run applied.
        resolution: What each of the design's literals means.
        values: The values the run filled in.
        text_fit: The result of fitting text to its boxes.

    Returns:
        A `Verified` carrying every problem found, what to say about it, and
        whether a replacement photo alone would fix it.

    Raises:
        Nothing. Both checks are expected to fail closed: an inspection that
        could not run is reported as a problem rather than as a pass.
    """
    # Verify what was actually written, not the value before the design's own
    # conventions were applied to it. A title filled as REALTOR to match the
    # placeholder's capitals was checked against "Realtor" and reported as
    # never having reached the flyer, on a flyer that plainly showed it.
    expected = {
        name: pairs[literal]
        for name, literal in resolution.fields.items()
        if literal in pairs and values.get(name, "").strip()
    }
    # A field nobody supplied keeps the design's own placeholder on purpose, so
    # its exact literal is not a leftover. Anything else still is.
    left_showing = tuple(
        literal for name, literal in resolution.fields.items() if not values.get(name, "").strip()
    )
    final_text = read_back(read_slide_text, output_id)
    verdict = judge_text(final_text or "", expected, 1, left_showing)
    # The gate is told exactly which sample text was left on purpose, because a
    # correct flyer showing the design's own price was being parked in review
    # for a comma the model read as a full stop. It is still asked to report
    # that text clipped or overlapping, which is the only thing Gable causes.
    seen: Inspection = look_at(run_id, thumbnail(output_id), left_showing)
    if left_showing:
        # Filtered afterwards as well: naming them in the prompt is guidance,
        # and the kind the model returns is the check that it was followed.
        seen = seen.without_expected_placeholders()

    non_visual = list(verdict.problems)
    if final_text is None:
        non_visual.insert(0, "the final text could not be read back for verification")
    if text_fit.unreadable:
        non_visual.append(
            f"the {text_fit.unreadable[0].text[:24]} would have to be shrunk so far "
            "it would be hard to read"
        )

    problems = list(non_visual)
    if not seen.checked:
        problems.append("the visual inspection could not run")
    elif not seen.confident:
        problems.append("the visual inspection was inconclusive")
    elif not seen.looks_right:
        problems.extend(seen.problems or ["the visual inspection found a problem"])

    if not problems:
        return Verified()

    if not seen.checked:
        spoken = safe("I rendered it, but I could not complete the visual inspection.")
    elif not seen.confident:
        spoken = safe("I rendered it, but the visual inspection was inconclusive.")
    else:
        spoken = seen.say or verdict.say or safe(f"I rendered it, but {problems[0]}")
    return Verified(
        problems=tuple(problems),
        spoken=spoken,
        # A source-photo contradiction has one clear recovery: the next thread
        # image replaces it on this run. Mixed or non-photo failures cannot be
        # fixed by a new photograph, so they stay in review.
        needs_replacement_photo=not non_visual and seen.needs_source_replacement,
    )


#: The exact run-event detail `live.refit_hero` records when a photo was too
#: small to fill its frame and was composed over a blurred copy of itself.
#: Matched rather than re-derived, so the sentence Carmen reads and the work
#: that was actually done cannot drift apart.
SMALL_SOURCE_DETAIL: Final[str] = "used deterministic small-source photo fitting"


def used_small_source_fit(connection: Connection, run_id: str) -> bool:
    """Whether this run had to enlarge a photo past what stays sharp.

    Args:
        connection: An open database connection.
        run_id: The run that built the flyer.

    Returns:
        True when the fit was composed from a source too small for the frame.

    Raises:
        sqlite3.Error: on a query failure.
    """
    row = connection.execute(
        "SELECT 1 FROM run_events WHERE run_id = ? AND detail = ? LIMIT 1",
        (run_id, SMALL_SOURCE_DETAIL),
    ).fetchone()
    return row is not None


def blank_note(left_blank: list[str]) -> str:
    """Say which values kept the design's placeholder, and how to fill them.

    Args:
        left_blank: Plain-words names of fields nobody supplied.

    Returns:
        One sentence, or empty when the flyer is complete. Naming them matters:
        the flyer is finished and useful, and Carmen still has to know exactly
        which two words on it are the design's rather than the listing's.

    Raises:
        Nothing.
    """
    kept = [name for name in left_blank if name.strip()]
    if not kept:
        return ""
    listed = kept[0] if len(kept) == 1 else ", ".join(kept[:-1]) + f" and {kept[-1]}"
    pronoun = "it" if len(kept) == 1 else "them"
    return (
        f"Nobody gave me the {listed}, so the design's own placeholder is still there. "
        f"Type over {pronoun}, or send {pronoun} here and I will run it again."
    )


def delivery_message(
    connection: Connection,
    run_id: str,
    *,
    output_url: str,
    run_notes: list[str],
    advisories: list[str],
    left_blank: list[str],
    price_missing_note: str = "",
) -> str:
    """Assemble the one message that delivers a finished flyer.

    Args:
        connection: An open database connection.
        run_id: The run that built it.
        output_url: The rendered Slides file.
        run_notes: Anything the research step wants to report.
        advisories: Correctable layout work Gable already did.
        left_blank: Plain-words names of fields nobody supplied.
        price_missing_note: The existing missing-price sentence, if any.

    Returns:
        One house-style message carrying the link and every useful detail, so
        the finished flyer arrives as a single thing to read rather than four.

    Raises:
        sqlite3.Error: on a query failure.
    """
    photo = photo_note(connection, run_id)
    fit = ""
    if used_small_source_fit(connection, run_id):
        # Said once, plainly. The photo is as good as that source allows, and
        # the only thing that improves it is a bigger original.
        fit = (
            "I did my best to fit this image to the frame. If you have a "
            "higher-quality version, send it here and I will run it again."
        )
    return safe(
        "\n".join(
            [
                f"Your flyer is ready. <{output_url}|Open the flyer>",
                *run_notes,
                *([photo] if photo else []),
                *([fit] if fit else []),
                *advisories,
                *([blank_note(left_blank)] if left_blank else []),
                *([price_missing_note] if price_missing_note else []),
            ]
        )
    )


def record_unhandled_failure(connection: Connection, run_id: str, reason: str) -> None:
    """Mark a run failed when even its outcome could not be recorded normally.

    Args:
        connection: An open database connection.
        run_id: The run that raised.
        reason: The recorded reason, naming the error's kind but never its
            message, which can carry a signed URL or an id.

    Raises:
        Nothing. This is the last thing standing between a raised run and a row
        that still says it is building, so it swallows its own failure too.
    """
    try:
        store.set_status(
            connection, run_id, "failed", "unhandled error during the run", failure_reason=reason
        )
    except Exception:
        logger.exception("could not record failure for run %s", run_id)


@dataclass(frozen=True, slots=True)
class Unfinished:
    """One reason a built flyer must not be called finished."""

    spoken: str
    detail: str


def fill_failure(pairs: dict[str, str], changed: int) -> Unfinished | None:
    """Why a fill that did not land exactly once stops the run.

    Two different situations. "No pairs" means the design has nothing Gable
    recognises — a Client Review layout is a client quote, a client name and
    stars, with no address or price because there is no property. Reporting that
    as a field failing to match sends whoever reads it looking for a text bug
    that is not there.

    Args:
        pairs: The find/replace pairs the fill was asked to make.
        changed: How many the API confirmed.

    Returns:
        What to say and what to record, or None when the fill was exact.

    Raises:
        Nothing.
    """
    if pairs and changed == len(pairs):
        return None
    if not pairs:
        return Unfinished(
            safe(
                "I do not know how to fill this design — none of its text matches "
                "anything I recognise. It may need a kind of information I do not "
                "collect yet."
            ),
            "this design has no fields Gable knows how to fill",
        )
    return Unfinished(
        safe(
            "I copied the design, but one of its fields did not match exactly once. "
            "I stopped before changing any text."
        ),
        "the template text did not match each intended field exactly once",
    )


def layout_failure(source: dict[str, Any], built: dict[str, Any]) -> Unfinished | None:
    """Why a built flyer moved its design's layout, if it did.

    Chase reported a band running off the page edge and elements running into
    each other on 2026-08-14, and the rendered inspection had reported neither.
    Rectangles are evidence where pixels were an opinion. Judged against this
    design's own geometry, so its deliberate bleeds stay silent.

    Args:
        source: The design as filed in Generic Templates.
        built: The finished copy.

    Returns:
        What to say and what to record, or None when nothing moved.

    Raises:
        Nothing.
    """
    moved = layout.regressions(source, built)
    if not moved:
        return None
    return Unfinished(
        safe(f"I built the flyer, but {moved[0][0].lower()}{moved[0][1:]}"),
        f"layout regression against the source design: {moved[0]}",
    )
