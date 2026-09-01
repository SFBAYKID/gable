"""What each template needs, per template — not one global column set.

Chase's finding, from reviewing two rendered flyers: **the templates do not
share a field set.** One carries open-house date and time and labelled bed/bath
counts; the other carries neither. Treating them as one schema is what produced
a flyer with the literal words "Phone" and "Website" on it, and another whose
"Website" wrapped mid-word in a box too narrow for a single word.

So a template declares its own fields and whether each is **required**. An empty
required field is a hard stop, never a flyer with a label where a value should
be. Text capacity is measured from the current Slides geometry by
``slides.preflight``; the old hand-entered character budgets were removed
because they could disagree with the source file after Carmen edited it.

`validate()` is pure and returns problems in Carmen's words. Nothing renders
until it comes back clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from gable.listings.address import WHOLE_ADDRESS, incomplete_address, zip_codes
from gable.listings.address import tidy as tidy_address

#: Field kinds, which decide how a value is checked.
TEXT: Final[str] = "text"
MONEY: Final[str] = "money"
ADDRESS: Final[str] = "address"
PHONE: Final[str] = "phone"
EMAIL: Final[str] = "email"
IMAGE: Final[str] = "image"
DATETIME: Final[str] = "datetime"


@dataclass(frozen=True, slots=True)
class Field:
    """One fillable slot on a template."""

    name: str
    kind: str = TEXT
    required: bool = False
    #: For an image slot: roughly what shape it is, so a headshot cannot be
    #: filled with a landscape photo.
    aspect: str = ""


@dataclass(frozen=True, slots=True)
class Manifest:
    """Every field one template has, and what it demands."""

    template: str
    fields: tuple[Field, ...] = ()

    def find(self, name: str) -> Field | None:
        """Look a field up by name."""
        return next((f for f in self.fields if f.name == name), None)


#: Slots common to most listing designs. Current source-box geometry supplies
#: capacity; this manifest only says which values may be left empty.
_ADDRESS = Field("address", ADDRESS, required=True)
_PRICE = Field("price", MONEY, required=True)
_AGENT = Field("agent_name", TEXT, required=True)
_PHONE = Field("agent_phone", PHONE, required=True)
_EMAIL = Field("agent_email", EMAIL, required=False)
_WEBSITE = Field("website", TEXT, required=False)
_BEDS = Field("beds", TEXT, required=True)
_BATHS = Field("baths", TEXT, required=True)
_SQFT = Field("square_feet", TEXT, required=True)
# Source uploads retain their full composition until the exact frame is
# measured. Rejecting a landscape source merely because the slide is portrait
# caused the image to be cropped once at upload and again at placement.
_HERO = Field("hero_photo", IMAGE, required=True, aspect="any")
_HEADSHOT = Field("headshot", IMAGE, required=False, aspect="square")
_OPEN_HOUSE = Field("open_house", DATETIME, required=True)

#: Per-template manifests. A template absent from here falls back to
#: `DEFAULT_LISTING`, which is deliberately conservative.
MANIFESTS: Final[dict[str, Manifest]] = {
    "Just Listed — Bracket Placeholders (cleanest)": Manifest(
        "Just Listed — Bracket Placeholders (cleanest)",
        (
            _ADDRESS,
            _PRICE,
            _BEDS,
            _BATHS,
            _SQFT,
            _AGENT,
            _PHONE,
            _EMAIL,
            _WEBSITE,
            _HERO,
            _HEADSHOT,
        ),
    ),
    "Just Listed — Plus Open House — Offered At": Manifest(
        "Just Listed — Plus Open House — Offered At",
        (_ADDRESS, _PRICE, _BEDS, _BATHS, _SQFT, _AGENT, _PHONE, _OPEN_HOUSE, _HERO, _HEADSHOT),
    ),
    "Just Sold — With Beds, Baths and SqFt": Manifest(
        "Just Sold — With Beds, Baths and SqFt",
        (_ADDRESS, _PRICE, _BEDS, _BATHS, _SQFT, _AGENT, _PHONE, _EMAIL, _HERO, _HEADSHOT),
    ),
    # A testimonial has no property, so no address and no price. Without its own
    # entry it fell to DEFAULT_LISTING, which requires an address, and row 5 was
    # asked to separate the street, city, state and ZIP of "Google Review, SRES
    # Listing 29 Maple".
    #
    # It has no property PHOTOGRAPH either, which this entry got wrong until
    # 2026-08-27: it listed `_HERO` and called it "the photograph its layout is
    # built around". Measured against the live file, the design has exactly one
    # image well -- 5.55x9.49in, width-over-height 0.58, portrait -- and that is
    # the agent's headshot, the same shape as Open House's real headshot. The
    # post is a quote and the person who earned it.
    #
    # Declaring a hero here is what asked Carmen for a property photo on
    # Porsher Howard's testimonial and kept asking after she answered, because
    # `runner` reads this manifest to decide whether to ask at all. See
    # `designs.NO_HERO_DESIGNS`.
    "Client Review Post": Manifest(
        "Client Review Post",
        (_AGENT, _PHONE, _EMAIL, _HEADSHOT),
    ),
}

#: What an unlisted template is assumed to need. Conservative on purpose: it is
#: better to ask about a field the design does not have than to ship a label.
DEFAULT_LISTING: Final[Manifest] = Manifest("default", (_ADDRESS, _AGENT, _PHONE, _HERO))


def manifest_for(template_name: str) -> Manifest:
    """The manifest for a template, or the conservative default.

    Args:
        template_name: The Drive file name.

    Returns:
        A `Manifest`.

    Raises:
        Nothing.
    """
    return MANIFESTS.get(template_name, DEFAULT_LISTING)


#: The one address shape every design prints; see `listings.address`.
ADDRESS_SHAPE: Final[re.Pattern[str]] = WHOLE_ADDRESS


def normalise_address(address: str) -> str:
    """Put an address into the canonical shape where possible.

    Args:
        address: As typed.

    Returns:
        `street, city, ST ZIP` when the parts can be identified, otherwise the
        input tidied. This never invents a ZIP — a missing one is a validation
        failure, not something to guess.

    Raises:
        Nothing.

    Note:
        `listings.address.tidy` is the whole rule set now. This used to hold a
        weaker second copy, then a second pass the runner applied and the
        thread announcement did not, so the announcement and the ask disagreed
        about the same address. Kept as a name so callers read as intended.
    """
    return tidy_address(address)


@dataclass(frozen=True, slots=True)
class Problem:
    """Something that stops a template being rendered, in Carmen's words."""

    field_name: str
    say: str
    #: True when Gable cannot proceed at all.
    blocking: bool = True
    #: True when the value is simply absent, false when one is present and
    #: wrong. Once the batched ask has gone out, an absent value is allowed to
    #: keep the design's own placeholder; a malformed one never is, because
    #: writing it onto a client-facing flyer states something untrue.
    absent: bool = False
    #: True when the value is present, imperfect, and still TRUE — so once it has
    #: been asked about it may be printed as supplied rather than asked about
    #: again. An address of "5556 Dolores Ave 21227" has a street and a ZIP and
    #: no city: incomplete, not false. Blocking a flyer on it asked Carmen for an
    #: address a second time, which is the round trip the one batched ask exists
    #: to remove. Distinct from `absent`, which keeps a placeholder; this prints
    #: what the person actually typed.
    releasable: bool = False


def validate(manifest: Manifest, values: dict[str, str]) -> list[Problem]:
    """Check a template's values before anything is rendered.

    Args:
        manifest: What this template needs.
        values: What is available, by field name.

    Returns:
        Problems, blocking ones first. Empty means it is safe to render.

    Raises:
        Nothing.
    """
    problems: list[Problem] = []

    for slot in manifest.fields:
        value = values.get(slot.name, "").strip()

        if slot.required and not value:
            readable = slot.name.replace("_", " ")
            question = "What number should it use?" if slot.kind == PHONE else "What should it say?"
            problems.append(
                Problem(
                    slot.name,
                    f"This design needs the {readable} and I do not have it. "
                    f"I have not built it rather than leaving a label on the flyer. {question}",
                    absent=True,
                )
            )
            continue

        if not value:
            continue

        if slot.kind == ADDRESS and not names_one_property(value):
            problems.append(
                Problem(
                    slot.name,
                    f"The address reads {value!r}, which looks like more than one "
                    "property. Which one is this post for?",
                )
            )
            continue

        if slot.kind == ADDRESS and not ADDRESS_SHAPE.match(value):
            # The same sentence the batched ask uses. This used to say "I could
            # not separate the street, city, state and ZIP confidently. What is
            # the full address?" — vague about which part was missing, and
            # asking for the whole thing back when Gable had already printed the
            # street twice in the same thread. See `address.incomplete_address`.
            problems.append(
                Problem(
                    slot.name,
                    incomplete_address(value),
                    releasable=_is_printable_partial_address(value),
                )
            )

    return sorted(problems, key=lambda p: not p.blocking)


#: More than one ZIP in a single address field means the field holds more than
#: one property. A real submission put two listings in it, separated by a line
#: break, and collapsing the break produced "5111 Hanover Pike Manchester, MD
#: 21102 75 S Ralph Street Westminstermd, 21157" — a single string naming
#: neither house. It fails the whole-address shape, so before this it would have
#: been printed as an incomplete-but-true address, which it is not: it is false
#: about both properties. A flyer carrying it would be wrong in the one way that
#: matters most, so it asks instead.
#:
#: The count leaves the house number out. This module used its own five-digit
#: pattern, which took "10600" in "10600 Partridge Ln Apt B3, Cockeysville, MD
#: 21030" for a ZIP and asked Carmen which of two properties a single condo was
#: — three times, on 2026-09-01, each time she said it was one. Five-digit house
#: numbers are ordinary; `listings.address.zip_codes` is the one ZIP reader.
def names_one_property(value: str) -> bool:
    """Whether an address field describes a single property.

    Args:
        value: The supplied address.

    Returns:
        False when it carries two or more ZIP codes, which is how a field
        holding two listings presents once its line breaks are collapsed.

    Raises:
        Nothing.
    """
    return len(zip_codes(value)) <= 1


#: A street line and a ZIP, with no confident city and state between them. This
#: is the shape a form submission arrives in when somebody typed the address
#: quickly, and it is printable: incomplete is not the same as untrue.
_STREET_AND_ZIP: Final[re.Pattern[str]] = re.compile(r"^\s*\d+\s+\S.*\b\d{5}(?:-\d{4})?\s*$")


def _is_printable_partial_address(value: str) -> bool:
    """Whether an address the shape check refused is still true enough to print.

    Args:
        value: The supplied address, already tidied.

    Returns:
        True when it opens with a street number and ends with a ZIP, which is a
        real address missing only its city and state. False for anything that
        does not identify a place at all, which must keep asking.

    Raises:
        Nothing.
    """
    if not names_one_property(value):
        return False
    return bool(_STREET_AND_ZIP.match(" ".join(value.split()).replace(",", " ")))


def needs_a_whole_address(manifest: Manifest, values: dict[str, str]) -> bool:
    """Whether this design has an address slot the supplied value cannot fill.

    Asked before the photograph rather than after it. An address that carries a
    ZIP but no state passes `intake.address_looks_usable` and is then refused
    here, and discovering that one step after the upload costs a person a whole
    extra round trip for a question that could have ridden the first.

    Args:
        manifest: The selected design's fields.
        values: The values this run intends to fill.

    Returns:
        True when the design shows an address and the one in hand is not a
        shape it can print. False when the design has no address slot at all,
        which is a client review, or when the address is already whole.

    Raises:
        Nothing.
    """
    if manifest.find("address") is None:
        return False
    supplied = values.get("address", "").strip()
    return bool(supplied) and not ADDRESS_SHAPE.match(supplied)
