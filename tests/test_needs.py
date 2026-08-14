"""Wording and gathering rules for the one batched ask."""

from __future__ import annotations

from gable.pipeline import needs


def test_a_photo_on_its_own_keeps_the_wording_the_thread_has_always_used() -> None:
    outstanding = needs.Needs(photo=True)

    assert outstanding.message() == "Can you send me the image?"
    assert outstanding.status() == "needs_photo"


def test_the_photo_and_the_values_arrive_as_one_message() -> None:
    outstanding = needs.Needs(photo=True)
    outstanding.add_values(["list_price", "beds", "square_feet"])

    message = outstanding.message()

    assert message.startswith("Can you send me the image?")
    assert "price, beds and square footage" in message
    assert "leave out" in message, "silence must be a usable answer"


def test_values_without_a_photo_pause_for_information_instead() -> None:
    outstanding = needs.Needs()
    outstanding.add_value("open_house")

    assert outstanding.message().startswith("I still need the open house date and time")
    assert outstanding.status() == "needs_info"


def test_the_same_value_named_by_two_checks_is_asked_for_once() -> None:
    """The form check and the research gate can both report a missing price."""
    outstanding = needs.Needs()
    outstanding.add_values(["list_price", "price", "beds"])

    assert outstanding.values == ["price", "beds"]


def test_nothing_outstanding_says_nothing() -> None:
    outstanding = needs.Needs()

    assert outstanding.anything is False
    assert outstanding.message() == ""


def test_the_one_ask_stays_inside_the_reply_ceiling() -> None:
    """`voice.safe` drops whole sentences past 600 characters."""
    from gable.voice import MAX_REPLY_CHARS, safe

    outstanding = needs.Needs(photo=True)
    outstanding.add_values(["list_price", "beds", "baths", "square_feet", "open_house"])

    message = outstanding.message()

    assert len(message) <= MAX_REPLY_CHARS
    assert safe(message) == message, "the whole ask must survive the house-style pass"


def test_a_missing_design_is_named_by_the_file_carmen_must_add() -> None:
    said = needs.missing_design("Under Contract")

    assert "Under Contract" in said
    assert "Generic Templates" in said


def test_a_blank_request_type_still_produces_a_usable_sentence() -> None:
    assert "this request type" in needs.missing_design("   ")
