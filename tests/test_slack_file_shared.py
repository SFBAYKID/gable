"""Turning Slack's separate `file_shared` notice into a placeable upload."""

from __future__ import annotations

from typing import Any

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


class _CaptionedClient:
    """Announces a file share and can be asked what was said beside it."""

    def __init__(self, text: str = "here is a better angle, run it again") -> None:
        """Bind the caption this client will report."""
        self.text = text
        self.replies_calls = 0

    def files_info(self, *, file: str) -> dict[str, Any]:
        """Report one image shared into one thread."""
        return {
            "file": {
                "id": file,
                "mimetype": "image/jpeg",
                "user": "U-CARMEN",
                "shares": {
                    "public": {"C0B02721MNK": [{"ts": "222.2", "thread_ts": "111.1"}]},
                },
            }
        }

    def conversations_replies(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        """Return the thread, including the message that carried the file."""
        self.replies_calls += 1
        return {
            "messages": [
                {"ts": "111.1", "text": "New Open House request"},
                {"ts": "222.2", "text": self.text},
            ]
        }


def test_the_words_sent_with_a_shared_file_are_recovered() -> None:
    """`file_shared` names the file, not the message, so the caption was lost.

    It is load-bearing twice: values stated beside a photo are recorded from
    it, and a delivered flyer only accepts a replacement when the words ask for
    one. The same upload therefore worked when Slack announced it as a message
    and silently did not when Slack announced it as a file share.
    """
    client = _CaptionedClient()

    shaped = shared_file_event({"file_id": "F1"}, client)

    assert shaped is not None
    assert shaped["text"] == "here is a better angle, run it again"
    assert shaped["thread_ts"] == "111.1"
    assert client.replies_calls == 1


def test_a_caption_that_cannot_be_read_never_costs_the_photo() -> None:
    """Empty is exactly what this route supplied before, so nothing regresses."""

    class _Broken(_CaptionedClient):
        def conversations_replies(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            raise RuntimeError("Slack is down")

    shaped = shared_file_event({"file_id": "F1"}, _Broken())

    assert shaped is not None
    assert shaped["text"] == ""
    assert shaped["files"][0]["id"] == "F1", "the photograph still arrives"
