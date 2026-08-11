"""Core domain types: the only shapes that cross a module boundary.

Every stage in ARCHITECTURE.md section 4 consumes one of these and produces
another, which is what keeps the orchestrator readable and each stage
independently testable.

Two decisions here carry more weight than the rest:

**`Listing.problems` instead of exceptions.** A malformed row must not take down
a batch (ARCHITECTURE.md 4.2). Normalization records what it could not parse and
the orchestrator decides; a missing description is not a crash.

**`response_row_id` is derived, never positional.** ARCHITECTURE.md 3.3 is
explicit: deriving it from the sheet row number means inserting or sorting rows
silently reassigns identities, and every flyer gets rebuilt. It is a hash of
(timestamp, agent email, address). See `derive_response_row_id` for exactly what
is normalized before hashing and why that choice cuts both ways.

Assumes: `Runs` column order matches `RUNS_HEADERS` below, taken from
ARCHITECTURE.md 3.3. If the live tab differs, that is a repository concern and
`RUNS_HEADERS` is the single place to correct.

Does not handle: persistence, I/O, or validation that needs the network.
`gable.sheets.repository` maps these to and from the Sheet; nothing in this
module imports anything that opens a socket.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

#: Length of the derived row identifier. 16 hex chars is 64 bits: at the volume
#: in ARCHITECTURE.md 8 (revisit above ~50 listings/day) a collision is not a
#: practical concern, and a short id stays readable in a Slack message.
RESPONSE_ROW_ID_CHARS: Final[int] = 16

#: `Runs.error` is truncated so one enormous traceback cannot make a sheet cell
#: unreadable. Google Sheets caps a cell at 50,000 characters; this is about
#: keeping the tab human-scannable, which is the whole reason the Sheet is the
#: datastore (ARCHITECTURE.md 3).
MAX_ERROR_CHARS: Final[int] = 500

_WHITESPACE_RUN = re.compile(r"\s+")


def utc_now() -> datetime:
    """Current time, always timezone-aware UTC.

    ARCHITECTURE.md 3.3 stores ISO 8601 UTC in `Runs`. A naive datetime would
    serialize without an offset and become ambiguous the moment anyone reads the
    tab from a different timezone.
    """
    return datetime.now(UTC)


def to_iso8601(moment: datetime) -> str:
    """Render a datetime as ISO 8601 UTC.

    Args:
        moment: Any datetime. Naive values are *assumed* UTC rather than
            silently interpreted as droplet-local time.

    Returns:
        An ISO 8601 string with a `+00:00` offset.
    """
    # ASSUMPTION: a naive datetime reaching here is UTC. Confirmed the moment
    # the Sheets client is written — Google Sheets returns timestamps without a
    # zone, and whether the form records them in UTC or in the sheet's locale is
    # unknown until we read the live tab. Tracked as blocking item 2.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


class RunStatus(StrEnum):
    """Lifecycle of one listing. Mirrors ARCHITECTURE.md 3.3 and AGENTS.md 6."""

    PENDING = "pending"
    NEEDS_PHOTO = "needs_photo"
    NEEDS_TEMPLATE = "needs_template"
    READY = "ready"
    DELIVERED = "delivered"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """True if this run is finished and must not be reprocessed.

        This is the idempotency test: the poller skips any row whose latest
        `Runs` status is terminal. `needs_photo` and `needs_template` are
        deliberately NOT terminal — they are paused, waiting on Carmen
        indefinitely, and are re-checked on `/gable run` (AGENTS.md 6).
        """
        return self in {RunStatus.DELIVERED, RunStatus.FAILED}

    @property
    def is_paused(self) -> bool:
        """True if this run is waiting on a human rather than on Gable."""
        return self in {RunStatus.NEEDS_PHOTO, RunStatus.NEEDS_TEMPLATE}


class PhotoSource(StrEnum):
    """Where a photo came from. The audit trail, not decoration.

    Order matches the cascade in CLAUDE.md section 8.
    """

    FORM = "form"
    DRIVE = "drive"
    BROKERAGE = "brokerage"
    WEB = "web"
    CARMEN = "carmen"
    GENERATED = "generated"

    @property
    def is_synthetic(self) -> bool:
        """True if no camera was ever pointed at this property."""
        return self is PhotoSource.GENERATED

    @property
    def is_human_supplied(self) -> bool:
        """True if a person chose this image; it must never be overwritten.

        AGENTS.md 4.4: a photo Carmen supplied is final.
        """
        return self in {PhotoSource.FORM, PhotoSource.CARMEN}


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """One row of the `Agents` tab (ARCHITECTURE.md 3.2)."""

    agent_email: str
    agent_name: str
    brokerage_url: str = ""
    canva_template_id: str = ""
    template_label: str = ""
    photo_folder_id: str = ""
    active: bool = True

    def __post_init__(self) -> None:
        """Normalize the join key.

        `agent_email` is the primary key joining form submissions to templates,
        and form respondents type their address by hand. Case and stray
        whitespace must not produce a second, unknown agent.
        """
        object.__setattr__(self, "agent_email", self.agent_email.strip().lower())

    @property
    def display_template(self) -> str:
        """What Carmen sees in Slack. Falls back to the id, then to a warning."""
        return self.template_label or self.canva_template_id or "(no template mapped)"


@dataclass(frozen=True, slots=True)
class PhotoResult:
    """A resolved photo plus everything needed to justify using it.

    `confidence` is required rather than optional on purpose. A source that
    cannot say how sure it is cannot be checked against
    `GABLE_PHOTO_MIN_CONFIDENCE`, and ARCHITECTURE.md 4.4 is unambiguous: a
    flyer with no photo gets caught by Carmen, a flyer with the wrong house
    ships.
    """

    url: str
    source: PhotoSource
    confidence: float
    ai_generated: bool = False
    ai_enhanced: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        """Enforce the invariants that keep a synthetic photo traceable.

        Raises:
            ValueError: if confidence is outside 0.0-1.0, or if a generated
                photo is not flagged `ai_generated`.
        """
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in 0.0-1.0, got {self.confidence}")
        # AGENTS.md 4.6 requires all three markers on any synthetic image. This
        # is the one that can be enforced in a type; the Slack badge and the
        # Runs flag are enforced where they are written. Making it impossible to
        # construct an unflagged generated photo removes the whole class of bug.
        if self.source.is_synthetic and not self.ai_generated:
            raise ValueError("a generated photo must be flagged ai_generated")

    def meets(self, threshold: float) -> bool:
        """True if this photo may be used without asking Carmen first.

        A human-supplied photo always qualifies: Carmen choosing an image is
        not something Gable second-guesses with a confidence score.
        """
        return self.source.is_human_supplied or self.confidence >= threshold


@dataclass(frozen=True, slots=True)
class Listing:
    """One normalized form submission.

    Construction never fails on bad data. Anything unparseable is recorded in
    `problems` and the orchestrator decides what to do (ARCHITECTURE.md 4.2).
    """

    response_row_id: str
    submitted_at: datetime
    agent_email: str
    agent_name: str
    address: str
    price_display: str = ""
    price_value: int | None = None
    agent_phone: str = ""
    description: str = ""
    description_truncated: bool = False
    beds: str = ""
    baths: str = ""
    sqft: str = ""
    form_photo_url: str = ""
    problems: tuple[str, ...] = ()

    @property
    def has_problems(self) -> bool:
        """True if normalization could not fully parse this row."""
        return bool(self.problems)

    @property
    def is_flyer_ready(self) -> bool:
        """True only if every field a flyer requires is populated.

        AGENTS.md 4.8 forbids reporting a flyer ready with any required field
        empty. A photo is not checked here — that is the resolver's answer, not
        the row's.
        """
        return bool(self.address and self.agent_name and self.agent_email and self.price_display)

    def with_problem(self, problem: str) -> Listing:
        """Return a copy with one more recorded problem.

        The dataclass is frozen, so a stage that discovers a late issue produces
        a new Listing rather than mutating one another stage may hold.
        """
        return replace_problems(self, (*self.problems, problem))


def replace_problems(listing: Listing, problems: tuple[str, ...]) -> Listing:
    """Return `listing` with its problems replaced.

    A module-level helper rather than `dataclasses.replace` inline, so the
    frozen-copy pattern has one obvious name.
    """
    return Listing(
        response_row_id=listing.response_row_id,
        submitted_at=listing.submitted_at,
        agent_email=listing.agent_email,
        agent_name=listing.agent_name,
        address=listing.address,
        price_display=listing.price_display,
        price_value=listing.price_value,
        agent_phone=listing.agent_phone,
        description=listing.description,
        description_truncated=listing.description_truncated,
        beds=listing.beds,
        baths=listing.baths,
        sqft=listing.sqft,
        form_photo_url=listing.form_photo_url,
        problems=problems,
    )


#: `Runs` column order, verbatim from ARCHITECTURE.md 3.3. Changing this
#: reorders a live append-only log, so it is the one place to edit and the
#: reason `RunRecord.to_row` exists rather than ad-hoc tuple building.
RUNS_HEADERS: Final[tuple[str, ...]] = (
    "run_id",
    "response_row_id",
    "address",
    "status",
    "photo_source",
    "photo_url",
    "ai_generated",
    "ai_enhanced",
    "output_file",
    "error",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One append to the `Runs` tab (ARCHITECTURE.md 3.3).

    Append-only: a state change writes a new row, it never edits an old one. The
    log is what explains how a listing reached its current state, and AGENTS.md
    6 calls a state that cannot be explained from it a bug.
    """

    run_id: str
    response_row_id: str
    address: str
    status: RunStatus
    photo_source: PhotoSource | None = None
    photo_url: str = ""
    ai_generated: bool = False
    ai_enhanced: bool = False
    output_file: str = ""
    error: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        """Truncate the error and enforce the synthetic-photo flag.

        Raises:
            ValueError: if the photo source is `generated` but `ai_generated`
                is False. AGENTS.md 4.6 requires the flag on every synthetic
                image, and the `Runs` tab is where that record has to survive.
        """
        if len(self.error) > MAX_ERROR_CHARS:
            object.__setattr__(self, "error", self.error[: MAX_ERROR_CHARS - 1] + "…")
        if self.photo_source is PhotoSource.GENERATED and not self.ai_generated:
            raise ValueError("photo_source=generated requires ai_generated=True")

    def to_row(self) -> list[str]:
        """Serialize to a `Runs` row in `RUNS_HEADERS` order.

        Booleans render as `TRUE`/`FALSE` so the Sheet shows them as booleans
        rather than as the strings "True"/"False", which sort and filter badly.
        """
        return [
            self.run_id,
            self.response_row_id,
            self.address,
            self.status.value,
            self.photo_source.value if self.photo_source else "",
            self.photo_url,
            "TRUE" if self.ai_generated else "FALSE",
            "TRUE" if self.ai_enhanced else "FALSE",
            self.output_file,
            self.error,
            to_iso8601(self.created_at),
            to_iso8601(self.updated_at),
        ]


def normalize_for_identity(text: str) -> str:
    """Reduce text to a form stable across cosmetic re-typing.

    Applies Unicode NFKC, casefolds, and collapses whitespace runs. Used only
    for hashing — never for display, and never for anything written to a flyer.
    """
    folded = unicodedata.normalize("NFKC", text).casefold().strip()
    return _WHITESPACE_RUN.sub(" ", folded)


def derive_response_row_id(submitted_at: str, agent_email: str, address: str) -> str:
    """Derive the stable idempotency key for a form row.

    ARCHITECTURE.md 3.3 requires this to be independent of sheet position, so
    inserting or sorting rows cannot reassign identities.

    The inputs are normalized (case-folded, whitespace-collapsed) before
    hashing. That cuts both ways and the tradeoff is deliberate:

    * Re-typing "123 Main St" as "123  main st" keeps the same identity, so a
      cosmetic edit does not rebuild a flyer that already shipped.
    * Two genuinely distinct submissions that agree on all three fields collapse
      to one identity — but agreeing on timestamp *and* email *and* address
      means it is the same submission.

    `submitted_at` is taken as the raw string from the sheet rather than a
    parsed datetime, because parsing it requires knowing the form's timezone,
    which is unknown until the live tab is read. The raw cell is stable
    regardless.

    Args:
        submitted_at: The Timestamp cell, exactly as read.
        agent_email: The submitting agent's email.
        address: The property address.

    Returns:
        A 16-character lowercase hex digest.
    """
    parts = (
        normalize_for_identity(submitted_at),
        normalize_for_identity(agent_email),
        normalize_for_identity(address),
    )
    # \x1f (unit separator) cannot appear in a spreadsheet cell, so it cannot be
    # smuggled in to make two different tuples hash identically.
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:RESPONSE_ROW_ID_CHARS]
