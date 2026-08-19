"""Turning Slack's separate `file_shared` notice into a placeable upload."""

from __future__ import annotations

from gable.slackapp.photos import shared_file_event


class _FilesInfoClient:
    """Return one configured `files.info` answer and record the lookup."""

    def __init__(self, answer: object, fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.asked: list[str] = []

    def files_info(self, *, file: str) -> object:
        self.asked.append(file)
        if self.fail:
            raise OSError("test Slack outage")
        return self.answer


def _info(**overrides: object) -> dict[str, object]:
    file_info: dict[str, object] = {
        "id": "F123",
        "mimetype": "image/jpeg",
        "user": "UCARMEN",
        "shares": {"public": {"C0BP597644B": [{"ts": "1787.9", "thread_ts": "1787.1"}]}},
    }
    file_info.update(overrides)
    return {"file": file_info}


def test_a_separately_announced_file_becomes_the_message_shaped_event() -> None:
    """Slack can attach the file after the message, leaving it with no files.

    Caleb Olawuyi's photo was lost that way on 2026-08-19 and Gable asked
    Carmen for an image she had already sent.
    """
    client = _FilesInfoClient(_info())

    shaped = shared_file_event({"file_id": "F123", "user_id": "UCARMEN"}, client)

    assert shaped is not None
    assert shaped["channel"] == "C0BP597644B"
    assert shaped["thread_ts"] == "1787.1"
    assert shaped["files"] == [{"id": "F123", "mimetype": "image/jpeg"}]
    assert client.asked == ["F123"]


def test_a_file_shared_into_two_places_is_left_alone() -> None:
    """Choosing between threads would put one listing's photo on another."""
    client = _FilesInfoClient(
        _info(
            shares={
                "public": {
                    "C0BP597644B": [{"ts": "1.1", "thread_ts": "1.0"}],
                    "C0B02721MNK": [{"ts": "2.1", "thread_ts": "2.0"}],
                }
            }
        )
    )

    assert shared_file_event({"file_id": "F123"}, client) is None


def test_a_non_image_upload_is_not_treated_as_a_property_photo() -> None:
    client = _FilesInfoClient(_info(mimetype="application/pdf"))

    assert shared_file_event({"file_id": "F123"}, client) is None


def test_a_file_with_no_share_and_a_failed_lookup_both_stay_silent() -> None:
    """Neither an unplaced file nor a Slack outage may guess at a thread."""
    assert shared_file_event({"file_id": "F123"}, _FilesInfoClient(_info(shares={}))) is None
    assert shared_file_event({"file_id": "F1"}, _FilesInfoClient(None, fail=True)) is None
    assert shared_file_event({}, _FilesInfoClient(_info())) is None
