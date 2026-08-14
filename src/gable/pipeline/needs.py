"""Everything one run still needs from a person, asked in a single message.

Gable used to pause once per missing thing: the new price, then the square
footage, then the photograph, each its own message and its own wait. Carmen
answered one question at a time and the flyer she wanted took six turns to
start. Chase's rule, 2026-08-13: ask for everything at once, build with what
comes back, and leave what nobody supplied as the design's own placeholder for
her to fill in.

So this module does two things. It collects outstanding needs while the run
walks its checks, and it words them as one ask. It decides nothing about what a
design requires — `slides.fields` reads that from the file — and it performs no
I/O.

The structural stops keep their own exact wording, at the bottom of this
module. A missing design file or an uncertified two-agent layout is not
something Carmen can answer in a thread the way a price is, so those are never
folded into the batched ask — but they are still what a run needs, and keeping
every one of these sentences in one file is what stops them drifting apart.

Does not handle: posting or persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from gable.listings.intake import Question

#: Research names the same value differently from the form. Anything absent
#: from this map is already the words a person would use.
_READABLE: Final[dict[str, str]] = {
    "list_price": "price",
    "square_feet": "square footage",
    "new_price": "new price",
    "open_house": "open house date and time",
}

#: Contradictions a person can answer in the same breath as the photograph, so
#: they ride the one batched ask instead of costing their own round trip.
#:
#: An address is the only one today. Chase, 2026-08-14, on a nineteen-message
#: thread: "If a user has to go back and forth 19 times they are just going to
#: build it themselves." Asking for the address, waiting, then asking for the
#: photograph is two waits for one person's attention. Batching it is safe
#: because it cannot leak: `slides.manifest.ADDRESS_SHAPE` refuses to build a
#: listing whose address is still unreadable, so an unanswered address stops
#: the flyer either way — it just stops without having cost an extra turn.
#:
#: A price reduction with no new price is NOT here. Nothing downstream can
#: catch it, so a silent answer would ship a price-reduction flyer with no
#: price on it.
BATCHABLE_CONTRADICTIONS: Final[frozenset[str]] = frozenset({"address"})


def joins_one_ask(question: Question) -> bool:
    """Whether this question can ride the batched ask rather than stop the run.

    Args:
        question: One thing the run needs from a person.

    Returns:
        True when leaving it unanswered is safe because a later gate still
        refuses to build, or when the value is simply absent — an absent value
        has always been allowed to fall back to the design's own placeholder.

    Raises:
        Nothing.
    """
    return question.absent or question.field_name in BATCHABLE_CONTRADICTIONS


#: The sentence that makes an unanswered item safe to leave out. It is the
#: whole reason one round of questions is enough: Carmen knows in advance that
#: silence is an answer, so she never has to reply to decline.
_LEAVE_OUT: Final[str] = (
    "Answer in one reply with whatever you have. Anything you leave out stays "
    "as the design's own placeholder for you to fill in."
)

#: Kept exactly as it was. A photo with nothing else outstanding is the common
#: case and this wording is what the thread has always opened with.
PHOTO_ONLY_ASK: Final[str] = "Can you send me the image?"


def readable(name: str) -> str:
    """Turn an internal field name into the words a person would use.

    Args:
        name: A field name such as `square_feet`, or already-plain words.

    Returns:
        The plain-words form, e.g. `square footage`.

    Raises:
        Nothing.
    """
    cleaned = name.strip()
    if not cleaned:
        return ""
    return _READABLE.get(cleaned, cleaned.replace("_", " "))


def _listed(items: list[str]) -> str:
    """Join names the way a person reads them: a, b and c."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


@dataclass
class Needs:
    """Outstanding requests gathered across one run's checks.

    Mutable by design: the runner adds to it as each check reports, then asks
    once at the end. Order is preserved and duplicates are dropped, so the same
    value named by both the form check and the research gate is asked for once.
    """

    #: Plain-words value names, in the order they were found.
    values: list[str] = field(default_factory=list)
    #: Whether the property photograph is still missing.
    photo: bool = False

    def add_value(self, name: str) -> None:
        """Record one missing value, ignoring blanks and repeats.

        Args:
            name: An internal field name or plain words.

        Raises:
            Nothing.
        """
        words = readable(name)
        if words and words not in self.values:
            self.values.append(words)

    def add_values(self, names: list[str]) -> None:
        """Record several missing values in order.

        Args:
            names: Internal field names or plain words.

        Raises:
            Nothing.
        """
        for name in names:
            self.add_value(name)

    @property
    def anything(self) -> bool:
        """Whether there is anything at all to ask for."""
        return self.photo or bool(self.values)

    def message(self) -> str:
        """The single ask covering every outstanding need.

        Returns:
            One message, or an empty string when nothing is outstanding. The
            photo comes first because it is the item that genuinely blocks a
            flyer; the values follow in one sentence, with the standing promise
            that silence leaves a placeholder rather than another question.

        Raises:
            Nothing.
        """
        if not self.anything:
            return ""
        if self.photo and not self.values:
            return PHOTO_ONLY_ASK
        listed = _listed(self.values)
        if self.photo:
            return f"{PHOTO_ONLY_ASK} I also need the {listed}. {_LEAVE_OUT}"
        return f"I still need the {listed}. {_LEAVE_OUT}"

    def status(self) -> str:
        """The paused state this ask leaves the run in.

        Returns:
            `needs_photo` whenever the photograph is outstanding, so a Slack
            upload can resume this exact run; `needs_info` when only values are.

        Raises:
            Nothing.
        """
        return "needs_photo" if self.photo else "needs_info"


#: The parser can prove who is listing and who is hosting, but the generic
#: template contract does not yet identify which text and photo objects belong
#: to each role. Filling repeated labels by page order produced a polished but
#: false flyer in the old partial path, so a two-agent request stops before
#: source selection until such a design has an explicit, testable slot contract.
TWO_AGENT_STOP: Final[str] = (
    "This request needs two agent placements, but that template layout is not "
    "certified yet. I cannot prove which text and photo spot belongs to the "
    "listing agent and which belongs to the hosting agent, so I have not built it."
)


def missing_design(request_type: str) -> str:
    """Say which design file is absent, by the exact name being looked for.

    A design is added by putting it in Generic Templates and calling it exactly
    what the form calls this request, so the useful sentence names the missing
    file rather than asking Carmen to nominate a category nobody outside the
    code has heard of.

    Args:
        request_type: The form's request type, as submitted.

    Returns:
        The stop message, naming the file it wanted.

    Raises:
        Nothing.
    """
    wanted = request_type.strip() or "this request type"
    return (
        f"I do not have a design named {wanted} in the Generic Templates "
        "folder, so I have not built anything. Add one with that exact "
        "name and I will use it."
    )
