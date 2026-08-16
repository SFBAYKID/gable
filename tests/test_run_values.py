"""Template values contain only facts supplied by an owned source."""

from __future__ import annotations

from pathlib import Path

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.pipeline.run_values import for_intake


def test_an_agent_title_is_not_invented_when_no_source_collects_one(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    store.upsert_salesperson(
        connection,
        email="agent@example.com",
        first_name="Avery",
        last_name="Agent",
        phone="410.555.0100",
    )
    intake = Intake(
        agent_email="agent@example.com",
        agent_name="Avery Agent",
        request_type="Sold",
        address="1 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )

    values = for_intake(connection, intake, {})

    assert values["agent_title"] == ""
    assert values["agent_phone"] == "410.555.0100"
    connection.close()


def test_a_sold_request_never_uses_a_public_list_price_as_its_closing_price(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = Intake(
        agent_email="agent@example.com",
        agent_name="Avery Agent",
        request_type="Sold",
        address="1 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )

    values = for_intake(connection, intake, {"list_price": "$515,000"})

    assert values["price"] == ""
    connection.close()


def test_a_listing_request_may_use_a_verified_public_list_price(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = Intake(
        agent_email="agent@example.com",
        agent_name="Avery Agent",
        request_type="New Listing",
        address="1 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )

    values = for_intake(connection, intake, {"list_price": "$515,000"})

    assert values["price"] == "$515,000"
    connection.close()


def _note_intake(request_type: str, post_details: str) -> Intake:
    """An intake carrying one request type and one details column."""
    return Intake(
        agent_email="agent@example.com",
        agent_name="Avery Agent",
        request_type=request_type,
        address="1 Main St, Baltimore, MD 21201",
        post_details=post_details,
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="Buyer",
        notes="",
    )


def test_a_short_deal_note_reaches_the_designs_note_panel(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)

    values = for_intake(
        connection,
        _note_intake(
            "Under Contract", "Under contract on the buyer side. Multiple offer situation."
        ),
        {},
    )

    assert values["listing_note"] == "Under contract on the buyer side. Multiple offer situation."
    connection.close()


def test_a_dismissed_details_column_is_not_printed_as_a_note(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)

    for written in ("", "Na", "n/a ", "None."):
        values = for_intake(connection, _note_intake("Under Contract", written), {})
        assert values["listing_note"] == "", written
    connection.close()


def test_a_note_of_pure_punctuation_keeps_the_designs_own_call_to_action(
    tmp_path: Path,
) -> None:
    """Row 110's details column holds a single "?".

    The strip list removed " .!" and not "?", so the flyer printed a callout
    panel containing nothing but a question mark where the design says
    "Ready to Buy? / DM me to find your next home." The visual gate caught it,
    which is the only reason it did not ship.
    """
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)

    for written in ("?", "??", "...", ".", "!!", " ? "):
        values = for_intake(connection, _note_intake("Under Contract", written), {})
        assert values["listing_note"] == "", written
    connection.close()


def test_a_note_that_carries_one_real_word_still_prints(tmp_path: Path) -> None:
    """The rule is "says nothing", not "is short"."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)

    values = for_intake(connection, _note_intake("Under Contract", "Sold in 3 days!"), {})

    assert values["listing_note"] == "Sold in 3 days!"
    connection.close()


def test_marketing_prose_is_not_squeezed_into_a_note_panel(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    prose = (
        "Move right in! This freshly updated 3-bedroom townhouse offers exceptional "
        "value in sought-after Howard County, with fresh paint throughout and new carpet."
    )

    values = for_intake(connection, _note_intake("New Listing", prose), {})

    assert values["listing_note"] == ""
    connection.close()


def test_a_reviews_prose_is_its_quote_and_never_a_note(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)

    values = for_intake(
        connection,
        _note_intake(
            "Client Review Post",
            "Rob Morgan\n\nGina was outstanding from the first showing through to settlement.",
        ),
        {},
    )

    assert values["listing_note"] == ""
    assert values["client_name"] == "Rob Morgan"
    connection.close()


def test_square_footage_is_grouped_the_way_every_design_writes_it(tmp_path: Path) -> None:
    """A researched 3663 rendered beside a sample reading 6,348 SQFT."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = _note_intake("Open House", "")

    assert for_intake(connection, intake, {"square_feet": "3663"})["square_feet"] == "3,663"
    assert for_intake(connection, intake, {"square_feet": "2,430"})["square_feet"] == "2,430"
    assert for_intake(connection, intake, {"square_feet": "980 sq ft"})["square_feet"] == "980"
    assert for_intake(connection, intake, {"square_feet": "Studio"})["square_feet"] == "Studio"
    connection.close()


def test_a_shorter_review_someone_sent_back_outranks_the_pasted_one(tmp_path: Path) -> None:
    """Rob Morgan's review is 1,028 characters against a panel drawn for 280."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = _note_intake(
        "Client Review Post",
        "Rob Morgan\n\nIn a simple word, Gina was outstanding from the first showing onward.",
    )
    store.remember_supplied_fact(
        connection, intake.address, "review_quote", "Gina was outstanding. She is the BEST!"
    )

    values = for_intake(connection, intake, {})

    assert values["review_quote"] == "Gina was outstanding. She is the BEST!"
    assert values["client_name"] == "Rob Morgan"
    connection.close()


def test_a_shorter_review_is_ignored_when_no_reviewer_could_be_read(tmp_path: Path) -> None:
    """A quote with nobody's name under it is not a testimonial."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = _note_intake("Client Review Post", "great agent")
    store.remember_supplied_fact(
        connection, intake.address, "review_quote", "Gina was outstanding. She is the BEST!"
    )

    values = for_intake(connection, intake, {})

    assert "review_quote" not in values
    connection.close()


def test_a_reviewer_named_in_a_reply_is_the_one_the_design_prints(tmp_path: Path) -> None:
    """A Zillow export writes "7/20/2026 - j E", which is not a name line."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = _note_intake("Client Review Post", "My wife and I had the best experience looking.")
    store.remember_supplied_fact(connection, intake.address, "client_name", "Jenna Ellis")
    store.remember_supplied_fact(
        connection, intake.address, "review_quote", "Ian is professional and experienced."
    )

    values = for_intake(connection, intake, {})

    assert values["client_name"] == "Jenna Ellis"
    assert values["review_quote"] == "Ian is professional and experienced."
    connection.close()


def test_a_note_that_only_repeats_the_request_type_is_not_printed(tmp_path: Path) -> None:
    """Row 25's details column reads "Under Contract", under a headline that does."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)

    for written in ("Under Contract", "under contract!", "Under Contract."):
        values = for_intake(connection, _note_intake("Under Contract", written), {})
        assert values["listing_note"] == "", written
    connection.close()


def test_an_open_house_answered_in_the_thread_is_the_one_that_is_printed(
    tmp_path: Path,
) -> None:
    """Gable asked, was given the date and time, and asked again."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = _note_intake("Open House", "")
    store.remember_supplied_fact(
        connection, intake.address, "open_house", "Saturday, Aug 22, 2026 1-3PM"
    )

    values = for_intake(connection, intake, {})

    assert values["open_house"] == "Saturday, Aug 22, 2026 1-3PM"
    connection.close()


def test_the_forms_own_open_house_is_used_when_nobody_answered(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    intake = Intake(
        agent_email="agent@example.com",
        agent_name="Avery Agent",
        request_type="Open House",
        address="1 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="08/01 12-2pm",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )

    assert for_intake(connection, intake, {})["open_house"] == "08/01 12-2pm"
    connection.close()
