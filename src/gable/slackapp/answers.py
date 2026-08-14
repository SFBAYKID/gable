"""Recording the listing values a person states, wherever they state them.

Gable asks for the photograph and every missing value in one message, so the
reply arrives in one of two shapes: a text answer, or a photo with the answers
in its caption. Both end up here, so a value is stored the same way whichever
one it came in.

Does not handle: deciding that a message contains values at all — that is the
conversational model's job — or resuming the run afterwards.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from gable.db import store
from gable.slackapp.brain import stated_values

logger = logging.getLogger("gable.slack.answers")


def record_stated(
    connection: sqlite3.Connection,
    address: str,
    arguments: dict[str, Any],
) -> int:
    """Store every listing value from one `supply_listing_value` call.

    Args:
        connection: An open database connection.
        address: The freshly re-read property address to file the values
            against, never a cached copy, so a stated fact belongs to the
            property the run is actually about.
        arguments: The tool call's arguments as the model returned them.

    Returns:
        How many values were recorded. Zero means nothing usable was stated,
        which the caller reports rather than treating as a silent success.

    Raises:
        Nothing. One unusable value is skipped rather than discarding the
        others sent in the same reply.
    """
    recorded = 0
    for field_name, value in stated_values(arguments):
        try:
            store.remember_supplied_fact(connection, address, field_name, value)
        except ValueError:
            logger.exception("a stated listing value was refused before storage")
            continue
        recorded += 1
    return recorded


def carries_a_value(text: str) -> bool:
    """Whether a message could contain one of the values Gable asks for.

    Every value the one batched ask can take is a number or a date, so text
    with no digit cannot carry one. Used to keep a bare "here you go" from
    costing a paid conversational call.

    Args:
        text: The message, as sent.

    Returns:
        True when it is worth reading for values.

    Raises:
        Nothing.
    """
    return any(character.isdigit() for character in text)
