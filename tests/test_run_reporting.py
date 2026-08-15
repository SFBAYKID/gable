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


def test_a_visual_lead_does_not_swallow_the_text_problem_listed_first() -> None:
    """The list puts text problems first; the lead is usually the visual judge.

    Iterating from problems[1:] assumed the lead covered problems[0], so a
    wrong price vanished whenever the picture also had a complaint.
    """
    price = "the price reads $32,500 rather than the supplied $325,000"
    crop = "the top of the house is cropped off"

    said = run_reporting._every_problem(f"I rendered it, but {crop}.", [price, crop])

    assert "$32,500" in said
    assert "cropped" in said
    assert said.count("cropped") == 1


def test_a_layout_opinion_is_dropped_once_geometry_measured_clean() -> None:
    """Tambria Eaton's flyer was parked for a footer the designer drew.

    The vision gate said "the footer slogan is clipped along the bottom edge";
    the geometric audit had measured the slogan at the identical spot in the
    design and the built flyer, 2.5 points inside the page. A rectangle that
    matches the design cannot be a defect Gable introduced.
    """
    from gable.pipeline.vision import Inspection, InspectionProblemKind, InspectionRemedy

    seen = Inspection(
        checked=True,
        confident=True,
        looks_right=False,
        problems=["The footer slogan is clipped along the bottom edge."],
        problem_kinds=(InspectionProblemKind.LAYOUT,),
        remedy=InspectionRemedy.REVIEW,
    )

    cleared = seen.without_measured_layout_kinds()

    assert cleared.looks_right
    assert not cleared.problems


def test_a_text_opinion_survives_a_clean_geometric_audit() -> None:
    """Geometry cannot see a wrong digit; the text kinds must stay."""
    from gable.pipeline.vision import Inspection, InspectionProblemKind, InspectionRemedy

    seen = Inspection(
        checked=True,
        confident=True,
        looks_right=False,
        problems=[
            "The price reads $32,500.",
            "The footer slogan is clipped along the bottom edge.",
        ],
        problem_kinds=(InspectionProblemKind.TEXT, InspectionProblemKind.LAYOUT),
        remedy=InspectionRemedy.REVIEW,
    )

    cleared = seen.without_measured_layout_kinds()

    assert not cleared.looks_right
    assert list(cleared.problems) == ["The price reads $32,500."]
