"""The four post-fill stops, and everything else as a note."""

from __future__ import annotations

from gable.pipeline import fill_check
from gable.slides import fields as template_fields

RESOLUTION = template_fields.Resolution(
    fields={"address": "[PROPERTY ADDRESS]", "price": "[PRICE]", "agent_phone": "Phone"}
)
VALUES = {
    "address": "7940 Oakwood Rd, Glen Burnie, MD 21061",
    "price": "$515,000",
    "agent_phone": "(443) 854-8554",
}
PAIRS = {
    "[PROPERTY ADDRESS]": VALUES["address"],
    "[PRICE]": VALUES["price"],
    "Phone": VALUES["agent_phone"],
}
OFFICE = "410.555.0100"


def _readback(*parts: str) -> str:
    return "\n".join(parts)


def test_an_exact_fill_has_nothing_to_say() -> None:
    readback = _readback(*VALUES.values())
    verdict = fill_check.check_fill(PAIRS, 3, readback, VALUES, RESOLUTION, OFFICE)

    assert verdict.stop is None
    assert verdict.notes == ()


def test_a_slot_still_showing_the_design_text_is_a_note_not_a_stop() -> None:
    readback = _readback(VALUES["address"], "[PRICE]", VALUES["agent_phone"])

    verdict = fill_check.check_fill(PAIRS, 2, readback, VALUES, RESOLUTION, OFFICE)

    assert verdict.stop is None
    assert "price" in verdict.notes[0] and "did not go onto the flyer" in verdict.notes[0]
    assert verdict.details == ("not filled: price",)


def test_a_value_that_reads_back_changed_stops() -> None:
    readback = _readback(VALUES["address"], "$515,0000", VALUES["agent_phone"])

    verdict = fill_check.check_fill(PAIRS, 3, readback, VALUES, RESOLUTION, OFFICE)

    assert verdict.stop is not None
    assert "not sent it as finished" in verdict.stop.spoken


def test_a_strangers_phone_number_stops() -> None:
    readback = _readback(*VALUES.values(), "Stacey Abbott 410.952.6193")

    verdict = fill_check.check_fill(PAIRS, 3, readback, VALUES, RESOLUTION, OFFICE)

    assert verdict.stop is not None
    assert "not this listing" in verdict.stop.detail


def test_an_unreadable_copy_stops() -> None:
    verdict = fill_check.check_fill(PAIRS, 3, None, VALUES, RESOLUTION, OFFICE)

    assert verdict.stop is not None
    assert "read back" in verdict.stop.detail


def test_a_design_with_nothing_gable_recognises_stops() -> None:
    verdict = fill_check.check_fill({}, 0, "sample text", {}, RESOLUTION, OFFICE)

    assert verdict.stop is not None
    assert "no fields" in verdict.stop.detail
