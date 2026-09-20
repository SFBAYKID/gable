"""Every slot a live design displays must resolve to a field Gable can fill.

A slot that does not resolve is never replaced, so the design's own sample --
a real Corner House agent's name, cell number or address -- prints on somebody
else's flyer. `tests/fixtures/live_design_text.json` is what the six designs in
Generic Templates actually say, refreshed by `tools/refresh_design_text.py`.

This is the check that was missing on 2026-09-20. Carmen edited three designs on
2026-08-26; their sample agent changed with them, `SAMPLE_AGENT_NAMES` did not,
and `agent_name` stopped resolving on half the catalogue. Nothing noticed: the
unit suite was green, and the canary -- which builds these designs with sample
values -- reported nothing wrong, because it checks fields, frames, fitting and
geometry rather than whether every slot was filled. It surfaced when a rehearsal
flyer built for Andy Jang came back carrying Lina Mariner's name above Andy's
phone, email and face.

Refresh the fixture and read the diff after any design edit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest

from gable.slides import fields
from gable.slides.placeholders import SAMPLE_AGENT_NAMES, SAMPLE_CONTACTS

FIXTURE: Final[Path] = Path(__file__).parent / "fixtures/live_design_text.json"

#: Text a design draws as its own furniture: a heading, a call to action, a
#: strapline. None of it is a slot, none is a person's detail, and none is ever
#: replaced. Everything else in a design must resolve.
DESIGN_FURNITURE: Final[frozenset[str]] = frozenset(
    {
        "JUST LISTED",
        "OPEN HOUSE",
        "Open House",
        "QUESTIONS ABOUT THIS PROPERTY?",
        "DM me!",
        "REALTOR",
        "Local experts. Modern approach. Exceptional results.",
        "REAL PEOPLE REAL RESULTS",
    }
)


def _designs() -> dict[str, list[str]]:
    """The live design text, as captured from Drive."""
    loaded: dict[str, list[str]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("design", sorted(_designs()))
def test_every_slot_a_design_displays_resolves_to_a_field(design: str) -> None:
    """Anything that is not the design's own furniture must be fillable."""
    resolved = fields.resolve(_designs()[design])
    unresolved = [
        text for text in resolved.unrecognised if " ".join(text.split()) not in DESIGN_FURNITURE
    ]

    assert unresolved == [], (
        f"{design} displays text Gable cannot fill: {unresolved}. "
        "An unresolved slot keeps the design's own sample, which is a real "
        "agent's detail. Add the pattern, or add it to DESIGN_FURNITURE if it "
        "is genuinely not a slot."
    )


@pytest.mark.parametrize("design", sorted(_designs()))
def test_every_design_can_fill_the_agent_it_names(design: str) -> None:
    """The agent's name, and their phone or email where drawn, must resolve.

    Every design puts the agent on the flyer. If the slot carrying their name
    does not resolve, the flyer goes out under whichever real colleague the
    design happens to ship with.
    """
    resolved = fields.resolve(_designs()[design])

    assert resolved.fields.get("agent_name"), f"{design} cannot fill the agent's name"
    for slot in ("agent_phone", "agent_email"):
        drawn = any(
            sample in text
            for text in _designs()[design]
            for sample in SAMPLE_CONTACTS
            if ("@" in sample) == (slot == "agent_email")
        )
        if drawn:
            assert resolved.fields.get(slot), f"{design} draws an {slot} it cannot fill"


def test_no_design_still_shows_a_sample_person_that_is_not_recorded() -> None:
    """A sample name present on a design but absent from the table is the bug.

    Stated as its own test because the table is the only thing standing between
    an edited design and a colleague's name on the wrong flyer, and the table
    is only correct until the next edit.
    """
    missing: list[tuple[str, str]] = []
    for design, texts in _designs().items():
        resolved = fields.resolve(texts)
        if not resolved.fields.get("agent_name"):
            named = [t for t in texts if len(t.split()) == 2 and t.replace(" ", "").isalpha()]
            missing.extend((design, text) for text in named)

    assert missing == [], (
        f"these designs name somebody Gable does not know is a sample: {missing}. "
        "Add them to SAMPLE_AGENT_NAMES after reading the live design."
    )


def test_the_tables_hold_only_plain_recorded_values() -> None:
    """Guards the tables themselves: no blanks, no duplicates, no stray spacing."""
    for table, name in ((SAMPLE_AGENT_NAMES, "names"), (SAMPLE_CONTACTS, "contacts")):
        assert all(value == value.strip() and value for value in table), name
        assert len(set(table)) == len(table), f"duplicate sample {name}"
