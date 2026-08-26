"""A question asked outside a listing thread still gets a real answer.

On 2026-08-26 Chase asked "yes build it" and then "Gable is this now built?"
in threads that carried no listing. The first spent six paid vision calls and
answered with a template report; the second said only "I could not match this
thread to a listing, so I have not changed anything." Both were true and
neither answered him.

Assumes: nothing touches Slack or Google. Every row is written by the test.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.slackapp.context import waiting_summary
from gable.voice import violations


def _waiting_listing(
    connection: sqlite3.Connection,
    response_id: str,
    agent: str,
    address: str,
    headline: str,
    question: str,
) -> str:
    """Record one submission paused on a question, the way a real run pauses."""
    store.record_submission(
        connection,
        response_id,
        48,
        response_id,
        Intake(
            agent_email="agent@example.com",
            agent_name=agent,
            request_type="Sold",
            address=address,
            post_details="",
            open_house="",
            new_price="",
            closing_price="",
            extra_notes="",
            side="",
            notes="",
        ),
    )
    run_id = store.start_run(connection, response_id).run_id
    store.prepare_run_question(
        connection,
        run_id,
        "needs_photo",
        question,
        headline=headline,
    )
    store.set_status(connection, run_id, "needs_photo", "waiting on a person")
    return run_id


def test_nothing_waiting_says_so_plainly(tmp_path: Path) -> None:
    """Silence is not an answer either."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)

    answer = waiting_summary(store.waiting_asks(connection))

    assert "nothing else is waiting" in answer
    assert "could not match" not in answer
    assert not violations(answer)
    connection.close()


def test_one_waiting_listing_is_named_with_what_it_needs(tmp_path: Path) -> None:
    """Asking "is this built?" names the listing and the thing it lacks."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    _waiting_listing(
        connection,
        "row-1",
        "Mike Kulnich",
        "1522 E Baltimore St, Baltimore, MD 21231",
        "New Sold request from Mike Kulnich — 1522 E Baltimore St, Baltimore, MD 21231",
        "Can you send me the image?",
    )

    answer = waiting_summary(store.waiting_asks(connection))

    assert "Mike Kulnich" in answer
    assert "1522 E Baltimore St" in answer
    assert "Can you send me the image?" in answer
    # The announcement's opening words are noise when the listing is being cited.
    assert "New Sold request from" not in answer
    assert not violations(answer)
    connection.close()


def test_every_waiting_listing_is_listed(tmp_path: Path) -> None:
    """Two listings waiting means two lines, not a shrug about this thread."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    _waiting_listing(
        connection,
        "row-1",
        "Mike Kulnich",
        "1522 E Baltimore St, Baltimore, MD 21231",
        "New Sold request from Mike Kulnich — 1522 E Baltimore St",
        "Can you send me the image?",
    )
    _waiting_listing(
        connection,
        "row-2",
        "Brittany Tawney",
        "108 Hirtland Ave, Hanover, PA 17331",
        "New Listing with Open House request from Brittany Tawney — 108 Hirtland Ave",
        "Can you send me the image? I also need the price.",
    )

    answer = waiting_summary(store.waiting_asks(connection))

    assert "Mike Kulnich" in answer
    assert "Brittany Tawney" in answer
    assert "I also need the price" in answer
    assert not violations(answer)
    connection.close()


def test_a_delivered_listing_is_not_still_waiting(tmp_path: Path) -> None:
    """Only a paused run is owed something; a finished one must not be listed."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    run_id = _waiting_listing(
        connection,
        "row-1",
        "Mike Kulnich",
        "1522 E Baltimore St, Baltimore, MD 21231",
        "New Sold request from Mike Kulnich — 1522 E Baltimore St",
        "Can you send me the image?",
    )
    store.set_status(connection, run_id, "delivered", "flyer sent")

    assert store.waiting_asks(connection) == ()
    assert "nothing else is waiting" in waiting_summary(store.waiting_asks(connection))
    connection.close()
