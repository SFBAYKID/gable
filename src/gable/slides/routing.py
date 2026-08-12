"""Choosing which template a submission gets, without ever copying one.

A template used to be duplicated into every agent's folder. That made Carmen's
edits unpropagatable: fixing the Sold design meant opening thirty-eight files,
and any she missed kept rendering the old layout with no sign anything was
wrong. One design has to mean one file.

So an agent's folder holds **only** the templates that are genuinely theirs.
Everything else resolves to the master folder at render time. Carmen edits the
master once and every agent picks it up on their next flyer, because nothing was
ever copied to go stale.

The rule:

    agent's folder has one for this request type   -> use it
    it does not                                    -> use the master
    neither has one, or either has several         -> stop and ask Carmen

Ambiguity is refused rather than resolved. Two designs filed under the same
request type is a filing mistake, and picking the alphabetically-first one would
render a real flyer off a coin flip and look deliberate.

Does not handle: reading Drive. Resolution is a pure function of two listings so
it can be tested without a network, and the caller does the fetching.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Source(Enum):
    """Where a chosen template came from."""

    AGENT = "agent"
    MASTER = "master"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Template:
    """One template file, as far as routing cares."""

    file_id: str
    name: str
    request_type: str


@dataclass(frozen=True, slots=True)
class Choice:
    """Which template to use, and why — or why none could be chosen."""

    template: Template | None
    source: Source
    reason: str = ""

    @property
    def ok(self) -> bool:
        """True when exactly one template was found."""
        return self.template is not None


def normalise(request_type: str) -> str:
    """Fold a request type to its comparable form.

    Args:
        request_type: The value as typed into the form.

    Returns:
        Lowercased and whitespace-collapsed. The form has produced both
        ``New Listing`` and ``just listed`` for the same intent, and a
        case-sensitive match would file the second one as an unknown type and
        stop a flyer that should have rendered.

    Raises:
        Nothing.
    """
    return " ".join(request_type.split()).lower()


def _matching(templates: list[Template], request_type: str) -> list[Template]:
    """Every template filed under this request type."""
    wanted = normalise(request_type)
    return [t for t in templates if normalise(t.request_type) == wanted]


def choose(
    request_type: str,
    agent_templates: list[Template],
    master_templates: list[Template],
) -> Choice:
    """Pick the template for a submission, preferring the agent's own.

    Args:
        request_type: The submission's request type, as typed.
        agent_templates: Everything in that agent's folder. Empty for the
            majority of agents, which is the normal case rather than a problem.
        master_templates: Everything in the master folder.

    Returns:
        A `Choice`. `template` is None when nothing matched or when the match
        was ambiguous, and `reason` says which so the caller can tell Carmen
        something specific rather than "no template".

    Raises:
        Nothing.
    """
    if not normalise(request_type):
        return Choice(None, Source.NONE, "the submission has no request type")

    mine = _matching(agent_templates, request_type)
    if len(mine) == 1:
        return Choice(mine[0], Source.AGENT)
    if len(mine) > 1:
        names = ", ".join(sorted(t.name for t in mine))
        return Choice(
            None,
            Source.NONE,
            f"this agent has {len(mine)} templates filed under {request_type!r}: {names}",
        )

    theirs = _matching(master_templates, request_type)
    if len(theirs) == 1:
        return Choice(theirs[0], Source.MASTER)
    if len(theirs) > 1:
        names = ", ".join(sorted(t.name for t in theirs))
        return Choice(
            None,
            Source.NONE,
            f"the master folder has {len(theirs)} templates filed under {request_type!r}: {names}",
        )

    return Choice(
        None,
        Source.NONE,
        f"no template is filed under {request_type!r}, in this agent's folder or the master",
    )
