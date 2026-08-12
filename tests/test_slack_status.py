"""What the working indicator must guarantee.

All of it is cosmetic, which is exactly why the tests are about failure: a
decoration that can break the flyer it decorates is worse than no decoration.
"""

from __future__ import annotations

from typing import Any

import pytest

from gable.slackapp.status import WAITING_REACTION, Working


class FakeClient:
    """Records calls and can be told to fail any of them."""

    def __init__(self, *, status_works: bool = True, reactions_work: bool = True) -> None:
        """Start with both Slack surfaces working unless told otherwise."""
        self.status_works = status_works
        self.reactions_work = reactions_work
        self.statuses: list[str] = []
        self.added: list[str] = []
        self.removed: list[str] = []

    def assistant_threads_setStatus(self, **kwargs: Any) -> None:  # noqa: ANN401, N802
        """Stand in for the assistant thread status API."""
        if not self.status_works:
            msg = "assistant threads not enabled for this app"
            raise RuntimeError(msg)
        self.statuses.append(str(kwargs.get("status", "")))

    def reactions_add(self, **kwargs: Any) -> None:  # noqa: ANN401
        """Stand in for reactions.add."""
        if not self.reactions_work:
            msg = "already_reacted"
            raise RuntimeError(msg)
        self.added.append(str(kwargs.get("name", "")))

    def reactions_remove(self, **kwargs: Any) -> None:  # noqa: ANN401
        """Stand in for reactions.remove."""
        if not self.reactions_work:
            msg = "no_reaction"
            raise RuntimeError(msg)
        self.removed.append(str(kwargs.get("name", "")))


def test_the_assistant_status_is_preferred_when_available() -> None:
    """It renders like a person typing, which is what Carmen recognises."""
    client = FakeClient()
    with Working(client, "C1", "111.1", "Rendering the flyer"):
        pass
    assert client.statuses[0].startswith("Rendering the flyer")
    assert client.added == [], "no reaction is needed when the status API works"


def test_the_status_is_cleared_afterwards() -> None:
    """A spinner left running says work is still coming when it is not."""
    client = FakeClient()
    with Working(client, "C1", "111.1"):
        pass
    assert client.statuses[-1] == ""


def test_it_falls_back_to_a_reaction_when_the_status_api_is_unavailable() -> None:
    """Most workspaces have not enabled assistant threads."""
    client = FakeClient(status_works=False)
    with Working(client, "C1", "111.1"):
        pass
    assert client.added == [WAITING_REACTION]
    assert client.removed == [WAITING_REACTION]


def test_the_indicator_is_cleared_even_when_the_work_raises() -> None:
    """A failed build must not leave the thread looking like it is still going."""
    client = FakeClient(status_works=False)
    with pytest.raises(ValueError, match="build failed"), Working(client, "C1", "111.1"):
        raise ValueError("build failed")
    assert client.removed == [WAITING_REACTION]


def test_a_broken_indicator_never_breaks_the_work() -> None:
    """Every Slack call here can fail, and none of them may propagate.

    Slack raises when a reaction is already present or already gone, and both
    happen in normal use — a retry, or two builds in one thread.
    """
    client = FakeClient(status_works=False, reactions_work=False)
    done = False
    with Working(client, "C1", "111.1"):
        done = True
    assert done, "the body must run even when every indicator call fails"


def test_it_reacts_to_a_specific_message_when_given_one() -> None:
    """The photo message is a better anchor than the thread parent."""
    client = FakeClient(status_works=False)
    with Working(client, "C1", "111.1", message_ts="222.2"):
        pass
    assert client.added == [WAITING_REACTION]
