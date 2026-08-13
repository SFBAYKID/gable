"""What each template needs, per template — not one global column set.

Chase's finding, from reviewing two rendered flyers: **the templates do not
share a field set.** One carries open-house date and time and labelled bed/bath
counts; the other carries neither. Treating them as one schema is what produced
a flyer with the literal words "Phone" and "Website" on it, and another whose
"Website" wrapped mid-word in a box too narrow for a single word.

So a template declares its own fields, and each field declares:

* whether it is **required** — an empty required field is a hard stop, never a
  flyer with a label where a value should be
* its **max_chars** — a measured budget, not a guess, so overflow is caught
  before rendering rather than mangled by fitting afterwards

`validate()` is pure and returns problems in Carmen's words. Nothing renders
until it comes back clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

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
    #: Measured from the box: how many characters fit at the design's own size.
    #: Zero means unmeasured, and unmeasured fields are not budget-checked.
    max_chars: int = 0
    #: For an image slot: roughly what shape it is, so a headshot cannot be
    #: filled with a landscape photo.
    aspect: str = ""

    def over_budget(self, value: str) -> bool:
        """Whether a value is too long for this slot."""
        return bool(self.max_chars) and len(value.strip()) > self.max_chars


@dataclass(frozen=True, slots=True)
class Manifest:
    """Every field one template has, and what it demands."""

    template: str
    fields: tuple[Field, ...] = ()
    #: Exact raster-art layer replaced by the hero photo. Imported PowerPoint
    #: photos arrive as ordinary shapes, so type and size cannot identify them
    #: safely; this id is measured per template instead.
    hero_object_id: str = ""

    def required_names(self) -> tuple[str, ...]:
        """Fields that must be filled for this template to be renderable."""
        return tuple(f.name for f in self.fields if f.required)

    def find(self, name: str) -> Field | None:
        """Look a field up by name."""
        return next((f for f in self.fields if f.name == name), None)


#: Slots common to most listing designs. Budgets are measured from the boxes on
#: the Corner House deck at their own font sizes, not guessed.
_ADDRESS = Field("address", ADDRESS, required=True, max_chars=42)
_PRICE = Field("price", MONEY, required=True, max_chars=12)
_AGENT = Field("agent_name", TEXT, required=True, max_chars=22)
_PHONE = Field("agent_phone", PHONE, required=True, max_chars=16)
_EMAIL = Field("agent_email", EMAIL, required=False, max_chars=30)
_WEBSITE = Field("website", TEXT, required=False, max_chars=24)
_BEDS = Field("beds", TEXT, required=True, max_chars=4)
_BATHS = Field("baths", TEXT, required=True, max_chars=4)
_SQFT = Field("square_feet", TEXT, required=True, max_chars=8)
# The Corner House deck is Instagram 4:5 throughout, so a hero is portrait.
_HERO = Field("hero_photo", IMAGE, required=True, aspect="portrait")
_HEADSHOT = Field("headshot", IMAGE, required=False, aspect="square")
_OPEN_HOUSE = Field("open_house", DATETIME, required=True, max_chars=28)

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
        # ASSUMPTION: Drive copies preserve Slides object ids. A copied live
        # template plus a rendered thumbnail would confirm this after any
        # template replacement; the source file was inspected on 2026-08-11.
        hero_object_id="p1_i3",
    ),
    "Just Listed — Plus Open House — Offered At": Manifest(
        "Just Listed — Plus Open House — Offered At",
        (_ADDRESS, _PRICE, _BEDS, _BATHS, _SQFT, _AGENT, _PHONE, _OPEN_HOUSE, _HERO, _HEADSHOT),
        # ASSUMPTION: This id remains stable until Carmen replaces the source
        # template. Re-running template certification would confirm it.
        hero_object_id="p1_i10",
    ),
    "Just Sold — With Beds, Baths and SqFt": Manifest(
        "Just Sold — With Beds, Baths and SqFt",
        (_ADDRESS, _PRICE, _BEDS, _BATHS, _SQFT, _AGENT, _PHONE, _EMAIL, _HERO, _HEADSHOT),
        # ASSUMPTION: This id remains stable until Carmen replaces the source
        # template. Re-running template certification would confirm it.
        hero_object_id="p1_i3",
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


#: "street, city, ST ZIP". One flyer carried a ZIP and another did not, so the
#: format is pinned and a missing ZIP fails validation rather than rendering.
ADDRESS_SHAPE: Final[re.Pattern[str]] = re.compile(r"^.+,\s*.+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$")

# ASSUMPTION: If an address already ends in a state and ZIP and the preceding
# text contains a recognized street suffix followed by words, those final words
# are the city. A larger sample of real form submissions would confirm whether
# the suffix list needs expanding; this deliberately refuses ambiguous strings.
_MISSING_CITY_COMMA: Final[re.Pattern[str]] = re.compile(
    r"^(?P<street>.+\b(?:ALLEY|ANNEX|ARCADE|AVENUE|AVE|BEND|BOULEVARD|BLVD|"
    r"CIRCLE|CIR|COURT|CT|DRIVE|DR|EXPRESSWAY|HIGHWAY|HWY|LANE|LN|PARKWAY|"
    r"PKWY|PLACE|PL|PLAZA|ROAD|RD|SQUARE|SQ|STREET|ST|TERRACE|TER|TRAIL|"
    r"TRL|WAY))\s+(?P<city>[A-Z][A-Z .'-]+),\s*(?P<state>[A-Z]{2}\s+\d{5}(?:-\d{4})?)$",
    re.IGNORECASE,
)


def normalise_address(address: str) -> str:
    """Put an address into the canonical shape where possible.

    Args:
        address: As typed.

    Returns:
        `street, city, ST ZIP` when the parts can be identified, otherwise the
        input with whitespace tidied. This never invents a ZIP — a missing one
        is a validation failure, not something to guess.

    Raises:
        Nothing.
    """
    # `listings.address.tidy` is the address rule set, and this used to hold a
    # weaker second copy of it. The copies drifted, as copies do: this one only
    # recognised an upper-case state, so a real submission ending "Baltimore Md
    # 21230" never gained its comma and the flyer check asked the agent to
    # retype an address they had already given correctly. The remaining steps
    # below run only on what `tidy` deliberately leaves alone.
    text = tidy_address(address)
    if not ADDRESS_SHAPE.match(text):
        text = re.sub(r"\s*,\s*", ", ", text)
        # "Baltimore MD 21228" -> "Baltimore, MD 21228"
        text = re.sub(r"(?<![,])\s+([A-Z]{2})\s+(\d{5})", r", \1 \2", text)
        missing_comma = _MISSING_CITY_COMMA.match(text)
        if missing_comma is not None:
            text = (
                f"{missing_comma.group('street')}, {missing_comma.group('city')}, "
                f"{missing_comma.group('state')}"
            )
    return text.strip(" ,")


@dataclass(frozen=True, slots=True)
class Problem:
    """Something that stops a template being rendered, in Carmen's words."""

    field_name: str
    say: str
    #: True when Gable cannot proceed at all.
    blocking: bool = True


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
                )
            )
            continue

        if not value:
            continue

        if slot.over_budget(value):
            readable = slot.name.replace("_", " ")
            problems.append(
                Problem(
                    slot.name,
                    # A statement, not a question. Gable does not stop for this
                    # — the fitter shrinks the text and the flyer is delivered —
                    # and a question nobody is waiting for an answer to is worse
                    # than saying nothing.
                    f"The {readable} is longer than this design's box, so I sized it down to fit.",
                    blocking=False,
                )
            )

        if slot.kind is ADDRESS and not ADDRESS_SHAPE.match(value):
            problems.append(
                Problem(
                    slot.name,
                    f"The address reads {value!r}, and I could not separate the street, "
                    "city, state and ZIP confidently. What is the full address?",
                )
            )

    return sorted(problems, key=lambda p: not p.blocking)
