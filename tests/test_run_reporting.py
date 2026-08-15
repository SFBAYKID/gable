"""What Gable says about a flyer it checked and would not send."""

from __future__ import annotations

from gable.pipeline import run_reporting


def _lead(problem: str) -> str:
    """Phrase one problem the way the rendered judge phrases its own sentence."""
    return f"I rendered it, but {problem[0].lower()}{problem[1:]}"


PHOTO = "The supplied property photo shows house number 444, which conflicts with 5 Elphin Ct."
SPELLING = "The open house line misspells “Saturday” as “Satuday.”"


def test_a_render_with_two_problems_says_both() -> None:
    """Row 91 found a conflicting house number and a misspelt day, and said one.

    Carmen would have replaced the photo, waited for the rebuild, and only then
    heard about "Satuday" — a whole round trip for something Gable already knew
    when it first spoke.
    """
    said = run_reporting._every_problem(_lead(PHOTO), [PHOTO, SPELLING])

    assert "444" in said
    assert "Satuday" in said
    # Paragraphs, never a list: the house style forbids bullets.
    assert "\n\n" in said
    assert "- " not in said


def test_the_lead_sentence_is_not_repeated_as_its_own_problem() -> None:
    """The judge's sentence is built around the first problem, not beside it."""
    said = run_reporting._every_problem(_lead(PHOTO), [PHOTO])

    assert said.count("444") == 1
    assert "\n\n" not in said


def test_a_judge_with_nothing_to_say_still_names_the_problem() -> None:
    said = run_reporting._every_problem("", ["the visual inspection found a problem"])

    assert said == "I rendered it, but the visual inspection found a problem"


def test_no_problems_and_no_lead_says_nothing() -> None:
    assert run_reporting._every_problem("", []) == ""
