"""Wording and gathering rules for the one batched ask."""

from __future__ import annotations

from gable.listings.address import tidy
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


# --- a question nobody has answered is the only one still asked ------------


def test_an_answered_question_is_not_asked_again() -> None:
    """Gable asked Jay Hinish's listing for its open house three times."""
    from gable.listings.intake import Question
    from gable.pipeline.needs import still_unanswered

    asked = [
        Question("open house date and time", "When is it?", absent=True),
        Question("price", "What is it?", absent=True),
    ]

    left = still_unanswered(asked, {"open_house": "Saturday, Aug 22, 2026 1-3PM"})

    assert left == ["price"]


def test_nothing_answered_leaves_every_question_standing() -> None:
    from gable.listings.intake import Question
    from gable.pipeline.needs import still_unanswered

    asked = [Question("open house date and time", "When is it?", absent=True)]

    assert still_unanswered(asked, {}) == ["open house date and time"]


def test_the_words_a_question_uses_map_back_to_the_field_it_fills() -> None:
    from gable.pipeline.needs import internal_name, readable

    for field_name in ("open_house", "list_price", "square_feet", "new_price", "beds"):
        assert internal_name(readable(field_name)) == field_name


def test_a_blocker_is_asked_with_the_photo_not_before_it() -> None:
    """Lina Mariner's listing asked for a headshot and nothing else.

    It was equally certain it would need the property photo, so Carmen would
    have answered, been asked again, and answered again.
    """
    outstanding = needs.Needs()
    outstanding.add_blocker("I could not find a headshot for Lina Mariner.", "needs_info")
    outstanding.photo = True

    message = outstanding.message()
    assert "headshot" in message
    assert "property photo" in message
    assert "\n\n" in message, "a blocker and an ask are different things"
    assert outstanding.status() == "needs_info"


def test_every_blocker_is_reported_not_only_the_first() -> None:
    """Reporting one hides the next until the first is fixed."""
    outstanding = needs.Needs()
    outstanding.add_blocker("First design problem.", "needs_info")
    outstanding.add_blocker("Second design problem.", "needs_info")
    outstanding.add_blocker("First design problem.")

    assert outstanding.blockers == ["First design problem.", "Second design problem."]
    assert "Second design problem." in outstanding.message()


def test_the_photo_is_named_only_when_a_blocker_sits_above_it() -> None:
    """The generic image ask under a headshot sentence names two images."""
    plain = needs.Needs()
    plain.photo = True
    assert plain.message() == needs.PHOTO_ONLY_ASK

    blocked = needs.Needs()
    blocked.photo = True
    blocked.add_blocker("Add a headshot image to Head Shots.", "needs_info")
    assert needs.PHOTO_ONLY_ASK not in blocked.message()
    assert "property photo" in blocked.message()


def test_a_blocker_alone_still_stops_the_build() -> None:
    """Nothing outstanding but a blocker must not fall through to building."""
    outstanding = needs.Needs()
    outstanding.add_blocker("More than one agent-photo spot.", "needs_info")
    assert outstanding.anything
    assert outstanding.status() == "needs_info"


def test_an_incomplete_address_is_named_rather_than_called_missing() -> None:
    """Caleb Olawuyi, 2026-08-19: Gable announced the address, then asked for it.

    The check is right — the design prints street, city, state and ZIP, and
    this submission has no state — but "I still need the address" contradicts
    the announcement Gable had just posted and reads as a fault of its own.
    """
    said = needs.incomplete_address("4216 Norfolk Avenue, Baltimore 21216")

    assert "4216 Norfolk Avenue, Baltimore 21216" in said, "show what is in hand"
    assert "no state" in said
    assert "I still need the address" not in said


def test_a_state_written_out_is_not_reported_as_a_missing_state() -> None:
    """Deborah Manarin's Sold, 2026-08-21, said twice with the state on show.

    `address.tidy` now folds the name into its code before this sentence is
    written, so the value that reaches here carries one. The sentence must
    never again name a fault the address it prints disproves.
    """
    said = needs.incomplete_address(tidy("2519 Ann Arbor Lane Bowie Maryland 20716"))

    assert "no state" not in said
    assert "MD" in said


def test_a_missing_zip_is_named_as_a_missing_zip() -> None:
    """Saying "not in the order the design prints" sends somebody hunting a typo."""
    said = needs.incomplete_address("2519 Ann Arbor Lane, Bowie, MD")

    assert "no ZIP code" in said
    assert "no state" not in said


def test_an_address_missing_both_says_so_once() -> None:
    """Two faults, one sentence — asking twice is what this module exists to stop."""
    said = needs.incomplete_address("2519 Ann Arbor Lane, Bowie")

    assert "no state or ZIP code" in said


def test_an_out_of_order_address_is_not_reported_as_a_missing_state() -> None:
    """A state that is present but misplaced gets the other half of the truth."""
    said = needs.incomplete_address("Baltimore MD 4216 Norfolk Avenue 21216")

    assert "no state" not in said
    assert "street, city, state and ZIP" in said


def test_a_refused_photo_is_neither_held_nor_asked_for_again() -> None:
    """Three states, not two.

    `runs.photo_url` survives a refusal as audit evidence for the image that
    was rejected, never as permission to reuse it. Saying "I have the property
    photo" from that URL would be false in the one case where Carmen most needs
    to know it is not, and the check that refused it owns the sentence.
    """
    refused = needs.Needs()
    refused.note_photo("http://images.example/wrong-house.jpg", rejected=True)

    assert refused.photo is False, "the refusing check asks for the replacement"
    assert refused.photo_in_hand is False, "and Gable must not claim to hold one"


def test_a_usable_photo_is_held_and_a_missing_one_is_asked_for() -> None:
    """The two ordinary states are unchanged."""
    held = needs.Needs()
    held.note_photo("http://images.example/house.jpg", rejected=False)
    assert (held.photo, held.photo_in_hand) == (False, True)

    missing = needs.Needs()
    missing.note_photo("", rejected=False)
    assert (missing.photo, missing.photo_in_hand) == (True, False)


def test_a_conjunction_between_lot_numbers_is_not_a_state() -> None:
    """Frank Lancelotta III's listing, 2026-08-28.

    "3500 Hawks Hill Rd, Lot #1 & OR Lot # 2" has no city, no state and no ZIP.
    The "OR" joining its two lots matched Oregon, so Gable reported the address
    as merely missing its ZIP — understating what it actually needed back.
    """
    said = needs.incomplete_address("3500 Hawks Hill Rd, Lot #1 & OR Lot # 2")

    assert "no state or ZIP code" in said
    # It shows what it holds rather than asking for the address from scratch.
    assert "3500 Hawks Hill Rd" in said


def test_a_real_oregon_address_still_has_its_state() -> None:
    """The position test must not cost a genuine OR, IN, ME, OK, HI or OH."""
    said = needs.incomplete_address("123 Main St, Portland, OR")

    assert "no state" not in said
