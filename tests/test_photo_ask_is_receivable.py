"""Whatever Gable asks for in a message, Gable can receive.

The invariant these tests guard was broken twice. On 2026-08-15 a flyer parked
in `needs_review` refused the replacement photo that was the only thing which
could fix it. On 2026-08-20 an Open House run posted one message containing
both a blocking design problem and "Separately, can you send me the property
photo?", parked in `needs_template`, and then answered Carmen's upload with
"This listing is not waiting for a photo". Both were the same defect: one
`status` column being asked to carry two simultaneous waits.

The fix records the ask itself on the run, so these tests are written against
every paused state rather than the two that happened to be reported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gable.db import store
from gable.db.schema import connect
from gable.pipeline import needs
from gable.slackapp.runtime import _may_build_without_a_photo
from tests.photo_support import (
    THREAD,
    FakeSlackClient,
    _event,
    _handoff,
    _paused_database,
)

#: Every state a run can pause in. Parametrising over the set rather than
#: listing states by hand is the point: a new paused status inherits the
#: invariant instead of quietly falling outside it.
PAUSED_STATES = sorted(store.PAUSED)


def _asked_for_a_photo_in(path: Path, run_id: str, status: str) -> None:
    """Park the run in one paused state having asked for the photograph."""
    connection = connect(path)
    store.set_status(connection, run_id, status, "asked for it", slack_thread_ts=THREAD)
    store.set_awaiting_photo(connection, run_id, True)
    connection.close()


@pytest.mark.parametrize("status", PAUSED_STATES)
def test_every_paused_state_that_asked_for_a_photo_accepts_one(
    tmp_path: Path,
    status: str,
) -> None:
    """No paused state may refuse the photograph it asked for."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    _asked_for_a_photo_in(path, run_id, status)
    seen: list[str] = []

    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    assert "not waiting for a photo" not in said, f"{status} refused the photo it asked for"
    assert seen == ["response-1", run_id], f"{status} did not resume its own run"


@pytest.mark.parametrize("status", PAUSED_STATES)
def test_a_paused_run_that_asked_for_nothing_still_declines_a_stray_upload(
    tmp_path: Path,
    status: str,
) -> None:
    """The widening is tied to the ask, not to being paused at all.

    A run stopped before it ever reached the photo question -- a design file
    Gable could not find, say -- has asked for nothing, and an image dropped
    into that thread is a stray upload rather than an answer.
    """
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(connection, run_id, status, "never got that far", slack_thread_ts=THREAD)
    store.set_awaiting_photo(connection, run_id, False)
    connection.close()
    seen: list[str] = []

    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    if status in {"needs_photo", "needs_review"}:
        # These two are receivable on the status alone and were before this
        # change; the ask column only ever widens the set.
        assert "not waiting for a photo" not in said
    else:
        assert seen == [], f"{status} rebuilt from an upload it never asked for"


@pytest.mark.parametrize("status", PAUSED_STATES)
def test_a_run_owed_its_photo_is_never_released_to_build_without_one(
    tmp_path: Path,
    status: str,
) -> None:
    """Building with blanks releases unknown values, never the photograph."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    _asked_for_a_photo_in(path, run_id, status)
    connection = connect(path)
    run = store.run_by_id(connection, run_id)
    assert run is not None

    assert _may_build_without_a_photo(connection, run) is False
    connection.close()


def test_the_live_open_house_ask_records_that_it_wants_a_photo() -> None:
    """The 2026-08-20 message, verbatim, against the state it parks in."""
    outstanding = needs.Needs()
    outstanding.photo = True
    outstanding.add_blocker(
        "I checked the Open House design before building. The open house would need "
        "about 227 percent more room, and shrinking it enough would take it below the "
        "8-point readability limit. Widen that section, then tell me to check the "
        "updated template again.",
        "needs_template",
    )

    message = outstanding.message()

    # The blocker still owns the status: widening the design is the part only a
    # person can do, and the reply that says it is done routes on that state.
    assert outstanding.status() == "needs_template"
    # And the photo ask still goes out in the same message, which is why the
    # runner must record `outstanding.photo` beside the status.
    assert needs.PHOTO_ASK_BESIDE_A_BLOCKER in message
    assert outstanding.photo is True


def test_a_repeated_blocker_says_the_photo_arrived() -> None:
    """Answering an upload with the identical paragraph reads as losing it."""
    outstanding = needs.Needs()
    outstanding.photo_in_hand = True
    outstanding.add_blocker(
        "The open house would need about 227 percent more room.", "needs_template"
    )

    message = outstanding.message()

    assert message.startswith(needs.PHOTO_HELD)
    assert needs.PHOTO_ASK_BESIDE_A_BLOCKER not in message
    assert outstanding.status() == "needs_template"


def test_nothing_says_it_holds_a_photo_while_still_asking_for_one() -> None:
    """The two flags are opposites; saying both would be a false statement."""
    outstanding = needs.Needs()
    outstanding.photo = True
    outstanding.photo_in_hand = True
    outstanding.add_blocker("The design needs more room.", "needs_template")

    message = outstanding.message()

    assert needs.PHOTO_HELD not in message
    assert needs.PHOTO_ASK_BESIDE_A_BLOCKER in message


def test_a_rejected_photo_invites_a_replacement_that_needs_no_magic_words(
    tmp_path: Path,
) -> None:
    """The one case where a replacement is certain was the hardest to send.

    When the visual check concludes the photo shows a different house, the
    flyer still goes out and the message says so -- Chase's 2026-08-17 rule.
    But a delivered run only accepted a new image alongside an exact phrase,
    and the `file_shared` route carries no text at all, so the reply Gable
    invited could not arrive. `needs_replacement_photo` was computed for this
    and nothing read it.
    """
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(
        connection,
        run_id,
        "delivered",
        "delivered with what the checks noticed",
        output_file_id="deck-1",
        output_url="https://docs.example/deck-1",
        photo_url="http://images.example/wrong-house.jpg",
        slack_thread_ts=THREAD,
    )
    store.set_awaiting_photo(connection, run_id, True)
    connection.close()
    seen: list[str] = []

    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    assert "not waiting for a photo" not in said
    assert seen == ["response-1", run_id], "the invited replacement rebuilt the flyer"


def test_a_stray_image_on_a_delivered_flyer_still_needs_the_words(tmp_path: Path) -> None:
    """The guard that stops an accident rebuilding a finished flyer stands."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(
        connection,
        run_id,
        "delivered",
        "delivered",
        output_file_id="deck-1",
        output_url="https://docs.example/deck-1",
        slack_thread_ts=THREAD,
    )
    store.set_awaiting_photo(connection, run_id, False)
    connection.close()
    seen: list[str] = []

    said = _handoff(path, seen).handle(_event(), FakeSlackClient())

    assert seen == [], "nothing was rebuilt"
    assert "not waiting for a photo" in said


def test_a_run_with_no_flyer_does_not_claim_to_have_left_one_unchanged(
    tmp_path: Path,
) -> None:
    """Leaving the current flyer unchanged describes a flyer that never existed."""
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    connection = connect(path)
    store.set_status(connection, run_id, "failed", "a processing step failed")
    connection.close()

    said = _handoff(path, []).handle(_event(), FakeSlackClient())

    assert "no flyer yet" in said
    assert "left the current flyer unchanged" not in said
