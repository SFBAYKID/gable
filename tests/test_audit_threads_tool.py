"""The thread audit reads a thread the way a person did."""

from __future__ import annotations

from typing import Any

from tools.audit_threads import audit_channel, audit_thread, render

BOT = "BGABLE"
LINK = "Your flyer is ready. <https://docs.google.com/presentation/d/abc/edit|Open the flyer>"


def _gable(text: str, ts: str) -> dict[str, Any]:
    return {"bot_id": BOT, "text": text, "ts": ts}


def _carmen(text: str, ts: str) -> dict[str, Any]:
    return {"user": "UCARMEN", "text": text, "ts": ts}


def test_a_clean_thread_is_not_flagged() -> None:
    report = audit_thread(
        [
            _gable("New Sold request from Mike Kulnich — 703 Perception Way", "1.0"),
            _gable("Can you send me the image?", "1.1"),
            _carmen("", "1.2"),
            _gable(LINK, "1.3"),
        ],
        BOT,
    )

    assert not report.flagged
    assert report.gable_messages == 2
    assert report.ends_with_link


def test_lina_mariners_thread_is_flagged_for_the_repeat_and_the_count() -> None:
    question = (
        "The address reads 'X', which looks like more than one property. "
        "Which one is this post for?"
    )
    report = audit_thread(
        [
            _gable(
                "New Under Contract request from Lina Mariner — 10600 Partridge Lane, B3", "1.0"
            ),
            _gable("I have this listing at 10600 Partridge Lane, B3, but it has no state.", "1.1"),
            _carmen("The address is ...", "1.2"),
            _gable("Can you send me the image?", "1.3"),
            _gable(question, "1.4"),
            _carmen("Is it one property.", "1.5"),
            _gable(question, "1.6"),
            _carmen("Apt b3", "1.7"),
            _gable("I did not understand that as one of the details I asked for.", "1.8"),
            _gable(question, "1.9"),
        ],
        BOT,
    )

    assert report.flagged
    assert report.gable_messages == 6
    assert len(report.repeats) == 1
    assert any("same sentence" in problem for problem in report.problems)
    assert any("no flyer link" in problem for problem in report.problems)


def test_an_escalation_is_flagged_even_when_a_link_follows() -> None:
    report = audit_thread(
        [
            _gable("New Sold request", "1.0"),
            _gable("I need the address.", "1.1"),
            _gable(
                "I have your reply, and I am still stuck. I will not ask again in this thread.",
                "1.2",
            ),
            _gable(LINK, "1.3"),
        ],
        BOT,
    )

    assert report.escalated
    assert any("stopped asking" in problem for problem in report.problems)


def test_only_threads_gable_opened_are_audited() -> None:
    class Client:
        def auth_test(self) -> dict[str, str]:
            return {"bot_id": BOT, "user_id": "UGABLE"}

        def conversations_history(self, **_kwargs: object) -> dict[str, Any]:
            return {
                "messages": [
                    {
                        "bot_id": BOT,
                        "text": "New Sold request",
                        "ts": "2.0",
                        "reply_count": 2,
                    },
                    {"user": "UCARMEN", "text": "hi", "ts": "3.0", "reply_count": 1},
                    {"bot_id": BOT, "text": "a note with no replies", "ts": "4.0"},
                ],
                "response_metadata": {"next_cursor": ""},
            }

        def conversations_replies(self, **kwargs: object) -> dict[str, Any]:
            assert kwargs["ts"] == "2.0"
            return {
                "messages": [
                    _gable("New Sold request", "2.0"),
                    _gable("Can you send me the image?", "2.1"),
                    _gable(LINK, "2.2"),
                ],
                "response_metadata": {"next_cursor": ""},
            }

    reports = audit_channel(Client(), "C0B02721MNK", days=7)

    assert [report.thread_ts for report in reports] == ["2.0"]
    assert "0 flagged" in render(reports)
