"""Putting the two photographs onto a flyer that has already been built.

A design carries up to two images and they fail differently. The property
photograph is the point of a listing flyer, so a flyer without it is not a
draft. The agent's headshot is a matter of identity: a delivered flyer once
carried one agent's name beside a different agent's face, which is worse than
carrying no face at all.

This module owns that stage and nothing else. It performs no Slides I/O of its
own -- the two placement callables do that -- so the decision about what
counts as unfinished stays testable without a live presentation.

Does not handle: building the flyer, fitting text, or the visual gate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final

from gable.voice import paragraphs

logger: Final[logging.Logger] = logging.getLogger(__name__)

#: What to say when the property photograph did not land. Read as the middle of
#: "I built the flyer, but I ...".
NO_PHOTO: Final[str] = "could not get the photo onto it"

#: What to say when a real headshot well kept the design's sample face.
NO_HEADSHOT: Final[str] = "could not replace the sample headshot with the agent's own photo"


def place_all(
    run_id: str,
    output_id: str,
    template_label: str,
    hero_photo_url: str,
    values: dict[str, str],
    *,
    carries_a_photo: bool,
    place_photo: Callable[[str, str, str, str], bool],
    place_headshot: Callable[[str, str, dict[str, str], str], bool | None],
    progress: Callable[[str], None] = lambda _note: None,
) -> str:
    """Place the property photograph and the agent's headshot.

    Args:
        run_id: The run being built, for the log line.
        output_id: The copied presentation to edit.
        template_label: The design's name, so the headshot search can tell a
            design with no property photograph from one whose hero it failed to
            find.
        hero_photo_url: The fitted, published property photo, or "".
        values: The run's resolved field values; `headshot` is read from it.
        carries_a_photo: Whether this design has a property photo well at all.
            False skips hero placement entirely rather than reporting a failure
            to place something that has nowhere to go -- see
            `slides.designs.NO_HERO_DESIGNS`.
        place_photo: Puts the hero photo on the flyer. True on success.
        place_headshot: Puts the agent's face on the flyer. True on success,
            None when the design has no recognisable slot, False when a slot
            was found and its replacement failed.
        progress: Optional note for the person waiting.

    Returns:
        "" when the flyer has every image it should have, otherwise the clause
        naming what is missing, for the caller to put in front of the person.

    Raises:
        Nothing. Both callables report failure by return value.
    """
    # A design with no property photograph has nothing to place, and a False
    # from a placement that never ran would read as "could not get the photo
    # onto it" -- which would fail every testimonial as unfinished.
    if carries_a_photo:
        progress("is placing the photo...")
        placed = place_photo(run_id, output_id, hero_photo_url, template_label)
    else:
        placed = True

    if not placed:
        return NO_PHOTO

    # The sample face is the most visible thing Gable gets wrong: one agent's
    # name beside another agent's photograph.
    headshot_url = values.get("headshot", "")
    if not headshot_url:
        return ""

    progress("is putting the agent's face on it...")
    result = place_headshot(output_id, headshot_url, values, template_label)
    if result is True:
        logger.info("replaced the sample headshot for run %s", run_id)
        return ""
    if result is None:
        # Best effort: a design with no headshot frame is a deliverable flyer.
        logger.info("the design has no recognised headshot slot for run %s", run_id)
        return ""
    logger.error("could not replace the sample headshot for run %s", run_id)
    return NO_HEADSHOT


def ignore_headshot(_file_id: str, _url: str, _values: dict[str, str], _template: str = "") -> None:
    """Default headshot placer for a runner built without a live Slides client.

    Args:
        _file_id: Unused.
        _url: Unused.
        _values: Unused.
        _template: Unused.

    Returns:
        None, which `place_all` reads as "this design has no headshot slot" --
        the one reading that leaves a flyer deliverable.

    Raises:
        Nothing.
    """
    return


def unfinished(unplaced: str) -> str:
    """What Gable says about a flyer that is built but missing an image.

    Args:
        unplaced: `NO_PHOTO`, `NO_HEADSHOT`, or another such clause, read as
            the middle of "I built the flyer, but I ...".

    Returns:
        Two paragraphs. The second is the one that matters: it says the draft
        was not sent as finished, so nobody goes looking for a link.

    Raises:
        Nothing.
    """
    return paragraphs(
        f"I built the flyer, but I {unplaced}.",
        "I have not sent it as finished.",
    )
