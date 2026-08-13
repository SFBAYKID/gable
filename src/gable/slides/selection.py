"""Choosing which design a submission gets.

**One folder, and the file's name is the form's word.** Templates live only in
`Templates / Generic Templates`, and each is named exactly what the Google Form
calls that request type — a submission saying `Sold` uses a file called `Sold`.

This replaced a scored catalogue of 45 designs that ranked candidates by reading
the submission's notes. The naming rule is better for the reason it was chosen:
Carmen maintains the designs, and a convention she can verify by looking at a
folder is one she can keep correct, whereas a ranking that infers intent from
her prose is not, and it fails quietly when it infers wrong.

Does not handle: reading Drive. The caller supplies the folder's contents so
this stays a pure function of a file list and a request type.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from gable.listings.intake import Intake

logger = logging.getLogger("gable.selection")


def template_picker(
    list_templates: Callable[[], list[dict[str, str]]],
) -> Callable[[str, Intake], tuple[str, str]]:
    """Find the design whose **name is the request type**, in the one folder.

    This is the contract Chase set with Carmen on 2026-08-12, and it replaces
    the scored catalogue: the form offers a fixed list of request types, and a
    template is named exactly one of them. "Sold" on the form means a design
    called "Sold" in Generic Templates. Nothing is ranked, inferred from notes,
    or chosen between — if the name is not there, Gable asks rather than
    picking something close.

    The reason it is a rule rather than a heuristic is that Carmen maintains
    the designs. A naming convention she can check by looking at a folder is
    one she can keep correct; a ranking that reads her notes is not.

    Args:
        list_templates: Returns the Generic Templates folder's files, each with
            `id` and `name`.

    Returns:
        A callable taking `(category, intake)` and returning `(file_id, name)`,
        empty when no design carries this request type's name. The category is
        accepted for the runner's existing seam and deliberately unused: the
        form's own wording is the key now, not a mapped category.

    Raises:
        Nothing.
    """

    def pick(_category: str, intake: Intake) -> tuple[str, str]:
        wanted = " ".join(intake.request_type.split()).casefold()
        if not wanted:
            return "", ""
        matches = [
            item
            for item in list_templates()
            if " ".join(str(item.get("name") or "").split()).casefold() == wanted
        ]
        if len(matches) == 1:
            return str(matches[0]["id"]), str(matches[0]["name"])
        if len(matches) > 1:
            # Two files with the same name is a filing mistake, and picking
            # either one renders a real flyer off a coin flip.
            logger.error("%d templates are named %r; refusing to choose", len(matches), wanted)
        return "", ""

    return pick
