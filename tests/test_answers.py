"""One reply answers the one question, so it carries several values."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.slackapp.answers import carries_a_value, record_stated
from gable.slackapp.brain import TOOLS, stated_values


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


def test_one_call_carries_every_value_the_person_stated() -> None:
    """A reply of 3 beds, 2 baths and $600,000 is one answer, not three."""
    arguments: dict[str, Any] = {
        "values": [
            {"field": "beds", "value": "3"},
            {"field": "baths", "value": "2"},
            {"field": "list_price", "value": "$600,000"},
        ]
    }

    assert stated_values(arguments) == [
        ("beds", "3"),
        ("baths", "2"),
        ("list_price", "$600,000"),
    ]


def test_a_malformed_entry_does_not_discard_the_others() -> None:
    arguments: dict[str, Any] = {
        "values": [
            {"field": "beds", "value": "3"},
            "not an object",
            {"field": "baths", "value": ""},
            {"field": "list_price", "value": "$600,000"},
        ]
    }

    assert stated_values(arguments) == [("beds", "3"), ("list_price", "$600,000")]


def test_the_same_field_twice_keeps_the_first_answer() -> None:
    arguments: dict[str, Any] = {
        "values": [
            {"field": "beds", "value": "3"},
            {"field": "beds", "value": "4"},
        ]
    }

    assert stated_values(arguments) == [("beds", "3")]


def test_the_older_single_field_shape_is_still_read() -> None:
    """A decision persisted before the list shape must not read as empty."""
    assert stated_values({"field": "beds", "value": "3"}) == [("beds", "3")]


def test_nothing_stated_is_reported_as_nothing() -> None:
    assert stated_values({}) == []
    assert stated_values({"values": []}) == []


def test_every_stated_value_reaches_storage(db: sqlite3.Connection) -> None:
    address = "3283 Doyle Place, Aberdeen, MD 21009"
    arguments: dict[str, Any] = {
        "values": [
            {"field": "beds", "value": "3"},
            {"field": "baths", "value": "2"},
        ]
    }

    recorded = record_stated(db, address, arguments)

    assert recorded == 2
    assert store.recall_supplied_facts(db, address) == {"beds": "3", "baths": "2"}


def test_a_field_storage_refuses_is_skipped_not_fatal(db: sqlite3.Connection) -> None:
    """One unusable value must not lose the others sent beside it."""
    address = "3283 Doyle Place, Aberdeen, MD 21009"
    arguments: dict[str, Any] = {
        "values": [
            {"field": "not_a_field", "value": "whatever"},
            {"field": "beds", "value": "3"},
        ]
    }

    recorded = record_stated(db, address, arguments)

    assert recorded == 1
    assert store.recall_supplied_facts(db, address) == {"beds": "3"}


@pytest.mark.parametrize(
    ("text", "worth_reading"),
    [
        ("3 beds, 2 baths, $600,000", True),
        ("Sunday 2-4pm", True),
        ("here you go", False),
        ("", False),
    ],
)
def test_only_a_message_that_could_hold_a_value_costs_a_paid_call(
    text: str,
    worth_reading: bool,
) -> None:
    """Every value the one ask can take is a number or a date."""
    assert carries_a_value(text) is worth_reading


def _submission(connection: sqlite3.Connection, address: str) -> str:
    """One stored submission whose address is whatever the form said."""
    store.record_submission(
        connection,
        "row-81",
        81,
        "2026-07-30T13:15:56",
        Intake(
            agent_email="tambria@cornerhouserealty.com",
            agent_name="Tambria Eaton",
            request_type="Open House",
            address=address,
            post_details="",
            open_house="08/01 and 08/02 from 12-2pm",
            new_price="$647,999",
            closing_price="",
            extra_notes="",
            side="",
            notes="",
        ),
        "hash-81",
        "Form Responses 1",
    )
    return "row-81"


def test_an_address_gable_could_not_read_is_answered_and_kept(db: sqlite3.Connection) -> None:
    """Row 81 arrived as '1011 Winged Foot Drive' and the answer must stick."""
    row_id = _submission(db, "1011 Winged Foot Drive")

    recorded = record_stated(
        db,
        "1011 Winged Foot Drive",
        {"values": [{"field": "address", "value": "1011 Winged Foot Dr, Bowie, MD 20721"}]},
        row_id,
    )

    assert recorded == 1
    stored = store.load_submission(db, row_id)
    assert stored is not None
    assert stored.intake.address == "1011 Winged Foot Dr, Bowie, MD 20721"


def test_re_reading_the_form_cannot_undo_a_stated_address(db: sqlite3.Connection) -> None:
    """Every resume re-reads the sheet, which still says the unusable thing."""
    row_id = _submission(db, "1011 Winged Foot Drive")
    record_stated(
        db,
        "1011 Winged Foot Drive",
        {"values": [{"field": "address", "value": "1011 Winged Foot Dr, Bowie, MD 20721"}]},
        row_id,
    )

    _submission(db, "1011 Winged Foot Drive")

    stored = store.load_submission(db, row_id)
    assert stored is not None
    assert stored.intake.address == "1011 Winged Foot Dr, Bowie, MD 20721"


def test_an_address_and_a_price_in_one_reply_both_land_on_the_new_address(
    db: sqlite3.Connection,
) -> None:
    """The price must not be filed against the address nobody could read."""
    row_id = _submission(db, "1011 Winged Foot Drive")

    recorded = record_stated(
        db,
        "1011 Winged Foot Drive",
        {
            "values": [
                {"field": "list_price", "value": "$647,999"},
                {"field": "address", "value": "1011 Winged Foot Dr, Bowie, MD 20721"},
            ]
        },
        row_id,
    )

    assert recorded == 2
    assert store.recall_supplied_facts(db, "1011 Winged Foot Dr, Bowie, MD 20721") == {
        "list_price": "$647,999"
    }
    assert store.recall_supplied_facts(db, "1011 Winged Foot Drive") == {}


def test_an_answer_that_is_still_not_an_address_is_refused(db: sqlite3.Connection) -> None:
    """Storing it would only reproduce the same pause with a value nobody sent."""
    row_id = _submission(db, "1011 Winged Foot Drive")

    recorded = record_stated(
        db,
        "1011 Winged Foot Drive",
        {"values": [{"field": "address", "value": "the one on the golf course"}]},
        row_id,
    )

    assert recorded == 0
    stored = store.load_submission(db, row_id)
    assert stored is not None
    assert stored.intake.address == "1011 Winged Foot Drive"


def test_an_address_with_no_submission_to_attach_it_to_is_skipped(db: sqlite3.Connection) -> None:
    """The photo-caption path has no submission id, and must not guess one."""
    recorded = record_stated(
        db,
        "1011 Winged Foot Drive",
        {"values": [{"field": "address", "value": "1011 Winged Foot Dr, Bowie, MD 20721"}]},
    )

    assert recorded == 0


def test_the_model_may_state_an_address_at_all() -> None:
    """Gable asks for the address, so the tool has to be able to accept one."""
    supply = next(
        tool["function"] for tool in TOOLS if tool["function"]["name"] == "supply_listing_value"
    )
    fields = supply["parameters"]["properties"]["values"]["items"]["properties"]["field"]["enum"]

    assert "address" in fields
