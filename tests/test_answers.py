"""One reply answers the one question, so it carries several values."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.slackapp.answers import carries_a_value, record_stated
from gable.slackapp.brain import stated_values


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
