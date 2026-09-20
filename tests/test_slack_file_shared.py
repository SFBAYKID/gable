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

    def conversations_replies(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"messages": [{"ts": "1787.9", "files": [{"id": "F123", "mimetype": "image/jpeg"}]}]}


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
                {
                    "ts": "222.2",
                    "text": self.text,
                    "files": [{"id": "F1", "mimetype": "image/jpeg"}],
                },
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
    assert [item["id"] for item in shaped["files"]] == ["F1"]
    # One read, then one settle read: Slack can attach a file to a message it
    # has already posted, so a message reporting a single image may simply be
    # mid-upload. Reading twice is what stops the second and third photo of a
    # batch being lost to whichever sibling event happens to arrive first.
    assert client.replies_calls == 2


def test_an_unreadable_upload_message_still_places_the_file_it_named() -> None:
    """A history failure costs the caption and the siblings, never the upload.

    Refusing outright was the safer-looking answer -- it cannot build a flyer
    from one photograph of three. But Slack does not redeliver what Gable
    declined, so the cost of a transient read failure was Carmen's photo
    vanishing with nothing said, which is the failure this route was added to
    fix in the first place. Falling back to the file Slack named is exactly
    what this route did before batches existed.
    """

    class Broken(_CaptionedClient):
        def conversations_replies(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            self.replies_calls += 1
            raise RuntimeError("Slack is down")

    client = Broken()
    shaped = shared_file_event({"file_id": "F1"}, client)

    assert shaped is not None
    assert [item["id"] for item in shaped["files"]] == ["F1"]
    assert shaped["text"] == ""
    # Bounded: two attempts, then the fallback. A longer wait is worse than one
    # photograph when somebody is watching a thread that has said nothing.
    assert client.replies_calls == 2


class _SlackResponse:
    """A reply shaped like `slack_sdk.web.SlackResponse`: mapping-like, not a dict."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Hold the payload the real class exposes through `.get`."""
        self.data = data

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Read one field, exactly as the SDK's own accessor does."""
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401
        """Index into the payload, exactly as the SDK's own accessor does."""
        return self.data[key]


def test_a_real_slack_reply_is_read_even_though_it_is_not_a_dict() -> None:
    """`SlackResponse` supports `.get` and is not a `dict`, and that mattered.

    Both reads here were guarded with `isinstance(answer, dict)`, so every live
    reply was discarded and `shared_file_event` returned None for every real
    upload from the day it was written — while every test passed, because the
    fakes hand back plain dicts. The route added on 2026-08-19 to stop a photo
    being lost therefore never ran once. Found by calling it against the real
    Slack API on 2026-09-20; this fixture is the shape that would have caught
    it without a live call.
    """

    class Client:
        def files_info(self, *, file: str) -> _SlackResponse:
            return _SlackResponse(
                {
                    "ok": True,
                    "file": {
                        "id": file,
                        "user": "U-CARMEN",
                        "mimetype": "image/jpeg",
                        "shares": {"public": {"C0B02721MNK": [{"ts": "222.2"}]}},
                    },
                }
            )

        def conversations_replies(self, **_kwargs: Any) -> _SlackResponse:  # noqa: ANN401
            return _SlackResponse(
                {
                    "messages": [
                        {
                            "ts": "222.2",
                            "text": "three for the template",
                            "files": [{"id": f"F{i}", "mimetype": "image/jpeg"} for i in (1, 2, 3)],
                        }
                    ]
                }
            )

    shaped = shared_file_event({"file_id": "F2"}, Client())

    assert shaped is not None
    assert [item["id"] for item in shaped["files"]] == ["F1", "F2", "F3"]
    assert shaped["text"] == "three for the template"
    assert shaped["channel"] == "C0B02721MNK"
