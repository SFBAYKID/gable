"""Under Contract's call-to-action panel carries the submission's own note.

Every text run on the live Under Contract design, read 2026-08-14. The design
has one free text block and no other place to put what an agent wrote about the
deal, so that block is what `listing_note` fills — and only when a note was
actually written.
"""

from __future__ import annotations

from gable.slides import fields

UNDER_CONTRACT_TEXT: list[str] = [
    "Under Contract",
    "Kelli Kulnich",
    "Realtor",
    "443.326.7170",
    "kellianne@cornerhouserealty.com",
    "Ready to Buy?\n \nDM me to find your next home.",
    "9975 Old Mill Rd \nEllicott City, MD 21042",
]


def test_the_call_to_action_panel_is_read_as_the_note_slot() -> None:
    resolution = fields.resolve(UNDER_CONTRACT_TEXT)

    assert resolution.fields["listing_note"] == "Ready to Buy?\n \nDM me to find your next home."
    assert resolution.unrecognised == []


def test_the_note_replaces_the_panels_own_words_verbatim() -> None:
    resolution = fields.resolve(UNDER_CONTRACT_TEXT)

    pairs = fields.replacements(
        resolution,
        {"listing_note": "Under contract on the buyer side. Multiple offer situation."},
    )

    # The raw literal, newlines included: `replaceAllText` searches the slide
    # verbatim, so a collapsed one would match nothing.
    assert pairs == {
        "Ready to Buy?\n \nDM me to find your next home.": (
            "Under contract on the buyer side. Multiple offer situation."
        )
    }


def test_with_no_note_the_design_keeps_its_own_call_to_action() -> None:
    resolution = fields.resolve(UNDER_CONTRACT_TEXT)

    pairs = fields.replacements(resolution, {"listing_note": "", "agent_name": "Sara Wolz"})

    assert "Ready to Buy?\n \nDM me to find your next home." not in pairs
    assert pairs["Kelli Kulnich"] == "Sara Wolz"


def test_a_designs_own_headline_is_never_mistaken_for_a_note() -> None:
    resolution = fields.resolve(["Ready to Buy?"])

    assert "listing_note" not in resolution.fields
