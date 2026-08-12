"""Deciding what to do about a template file: measure it, reuse it, or re-measure.

A template's measurement is only worth storing if something says when it stops
being true. Carmen edits designs in place and re-exports from Canva, and neither
announces itself — so a stored measurement that is never re-checked is a
description of a design that used to exist.

The check is two-stage on purpose, because the cheap stage answers almost every
time:

    Drive `version` unchanged   -> reuse. No Slides call at all.
    changed                     -> fetch and re-measure, then compare fingerprints.
      fingerprint unchanged     -> opened and saved with no edit. Update the cursor.
      fingerprint changed       -> a new version, unconfirmed, with a list of
                                   exactly what moved.

That middle branch matters more than it looks: without it, every time Carmen
opens a file to look at it, Gable would cut a new version and send it back for
confirmation. The system would ask to be re-certified for doing nothing.

Nothing here decides whether a design is *good*. A new version is recorded as
unconfirmed and stays that way until a person says otherwise, because the
measurement contains inferences — which shape is the photo well, which literal
takes the price — and an inference nobody checked is a guess that has been
written down.

Does not handle: Drive or Slides I/O. Both are passed in, so the decision is a
pure function and testable without a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from gable.slides.measure import TemplateMeasurement, differences


class Outcome(Enum):
    """What checking a template concluded."""

    #: Never seen before. Measured and recorded as version 1, unconfirmed.
    NEW = "new"
    #: Unchanged since the stored measurement. Reused, nothing fetched.
    UNCHANGED = "unchanged"
    #: Opened and saved, but nothing about the design differs.
    TOUCHED = "touched"
    #: A real edit. A new version, unconfirmed until someone accepts it.
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class StoredVersion:
    """A measurement that has already been taken and written down."""

    template_id: str
    version_number: int
    structural_fingerprint: str
    geometry_fingerprint: str
    drive_version: str
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    """What to do about this template, and what to tell a person."""

    outcome: Outcome
    #: The measurement to use. None when nothing was re-measured.
    measurement: TemplateMeasurement | None = None
    #: The version number this produces. Unchanged for a reuse.
    version_number: int = 1
    #: What differs from the previous version, in words. Empty otherwise.
    changes: list[str] = field(default_factory=list)
    #: True when a flyer may be rendered from this template right now.
    usable: bool = False
    #: One sentence for Carmen. Empty when nothing needs saying.
    say: str = ""


def needs_fetch(stored: StoredVersion | None, drive_version: str) -> bool:
    """Whether the presentation has to be read at all.

    Args:
        stored: The most recent stored version, or None if never seen.
        drive_version: Drive's `version` field for the file right now.

    Returns:
        True when the file must be fetched and measured. False is the common
        case and costs one cheap Drive metadata call rather than a full
        presentation read.

    Raises:
        Nothing.

    Note:
        Drive's `version` increments on every server-side change, including ones
        invisible to a person. That makes it a strict signal — it never misses an
        edit — and a noisy one, which is why a fingerprint comparison follows it.
    """
    if stored is None:
        return True
    return stored.drive_version != drive_version


def decide(
    stored: StoredVersion | None,
    drive_version: str,
    measurement: TemplateMeasurement | None,
    previous: TemplateMeasurement | None = None,
) -> Decision:
    """Work out what this template's state means.

    Args:
        stored: The most recent stored version, or None if never seen.
        drive_version: Drive's `version` for the file right now.
        measurement: A fresh measurement, or None if `needs_fetch` said no.
        previous: The stored version's measurement, when available. Used only to
            describe what changed; its absence degrades the report, not the
            decision.

    Returns:
        A `Decision` saying whether the template is usable now, what version this
        is, and what to tell Carmen.

    Raises:
        ValueError: if a fetch was required but no measurement was supplied.
            Guessing here would mean reporting a template as unchanged without
            having looked at it.
    """
    if not needs_fetch(stored, drive_version):
        assert stored is not None  # needs_fetch returns True when stored is None
        return Decision(
            outcome=Outcome.UNCHANGED,
            version_number=stored.version_number,
            usable=stored.confirmed,
            say="" if stored.confirmed else "This template has not been confirmed yet.",
        )

    if measurement is None:
        msg = "the template changed and must be measured before it can be judged"
        raise ValueError(msg)

    if stored is None:
        return Decision(
            outcome=Outcome.NEW,
            measurement=measurement,
            version_number=1,
            usable=False,
            say=(
                "This is a template I have not seen before. I have measured it and "
                "written it down, but I would like it checked before I build a flyer "
                "from it."
            ),
        )

    if stored.structural_fingerprint == measurement.structural_fingerprint:
        # Opened and saved with nothing altered. Move the cursor, keep the
        # version, and say nothing — this happens whenever Carmen looks at a file.
        return Decision(
            outcome=Outcome.TOUCHED,
            measurement=measurement,
            version_number=stored.version_number,
            usable=stored.confirmed,
            say="",
        )

    changes = differences(previous, measurement) if previous is not None else []
    moved = len(changes)
    detail = f" I can see {moved} change{'s' if moved != 1 else ''}." if moved else ""
    return Decision(
        outcome=Outcome.CHANGED,
        measurement=measurement,
        version_number=stored.version_number + 1,
        changes=changes,
        usable=False,
        say=(
            f"This template has changed since I last measured it, so I have recorded "
            f"version {stored.version_number + 1}.{detail} I would like it checked "
            "before I build any more flyers from it."
        ),
    )
