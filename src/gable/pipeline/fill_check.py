"""What the readback says about the fill: a stop, or notes for under the link.

A delivered flyer once carried "$460,0000" against a submission that supplied
"$685,000", and every check passed, because the vision pass reads layout and
a plausible-looking wrong number is not a layout problem. Counting
replacements is not enough either: `replaceAllText` reported success while
corrupting the text it matched inside. So the flyer is read back and every
value must appear exactly as supplied.

Two kinds of "missing" come out of that read, and since 2026-09-01 they go
different ways. A slot still showing the design's own literal is a fill that
did not land: the flyer is delivered and the slot is named under the link. A
slot showing neither the literal nor the value is a corrupted fact about a
real house, and that flyer is never delivered. A phone number or email this
run did not supply is somebody else's, and that never goes out either.

Pure: the reads are done by the caller. Does not handle: image placement or
the geometric audit, which have their own notes.
"""

from __future__ import annotations

from dataclasses import dataclass

from gable.pipeline import audit, run_reporting
from gable.slides import fields as template_fields
from gable.voice import safe


@dataclass(frozen=True, slots=True)
class FillVerdict:
    """Either a reason to hold the flyer, or what to say under its link."""

    stop: run_reporting.Unfinished | None = None
    notes: tuple[str, ...] = ()
    details: tuple[str, ...] = ()


def check_fill(
    pairs: dict[str, str],
    changed: int,
    readback: str | None,
    values: dict[str, str],
    resolution: template_fields.Resolution,
    office_phone: str,
) -> FillVerdict:
    """Judge a filled copy from its readback.

    Args:
        pairs: The literal-to-value substitutions the fill was asked to make.
        changed: How many the API confirmed, or -1 for a fill refused as unsafe.
        readback: All text read back from the copy, or None when Slides would
            not return it.
        values: Everything the run intended to place.
        resolution: Which literal each field replaced.
        office_phone: The brokerage's own line, allowed on any flyer.

    Returns:
        A `FillVerdict`. `stop` is set for the four cases ARCHITECTURE.md
        §4.7b keeps as stops; otherwise `notes` carries the sentences for
        under the link and `details` the fragments for the run event.

    Raises:
        Nothing.
    """
    unfinished = run_reporting.fill_failure(pairs, changed)
    if unfinished is not None and not pairs:
        # Nothing of the listing is on it, so it is the design, not a flyer.
        return FillVerdict(stop=unfinished)
    if readback is None:
        return FillVerdict(
            stop=run_reporting.Unfinished(
                safe(run_reporting.UNREADABLE_FLYER),
                "the filled flyer could not be read back for verification",
            )
        )
    sent = {value for value in pairs.values() if value.strip()}
    missing = audit.values_missing_from(readback, values, sent)
    unfilled, wrong = run_reporting.readback_split(readback, values, resolution, missing)
    stray: list[str] = []
    if not wrong:
        # A phone number or email on the flyer that this run did not supply
        # belongs to the template's sample agent. A delivered flyer carried
        # "Stacey Abbott, 410.952.6193, sabbotthomes@gmail.com" from a
        # two-agent design's second slot and passed every check, because the
        # readback can only verify that supplied values appear.
        stray = audit.foreign_content_in(readback, values, office_phone)
    if wrong or stray:
        detail = (
            f"a filled value did not read back correctly: {wrong[0]}"
            if wrong
            else f"contact details that are not this listing's: {stray[0]}"
        )[:400]
        # Two different problems, two different sentences; splicing them once
        # produced "the phone number ... is not this listing's on it does not
        # match what I was given".
        spoken = safe(run_reporting.mismatch(wrong[0] if wrong else "", stray))
        return FillVerdict(stop=run_reporting.Unfinished(spoken, detail))
    if not unfilled:
        return FillVerdict()
    return FillVerdict(
        notes=(safe(run_reporting.unfilled_note(unfilled)),),
        details=(f"not filled: {', '.join(unfilled)}",),
    )
