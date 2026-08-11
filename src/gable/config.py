"""Frozen application settings, parsed once from the environment at startup.

Every variable documented in `.env.example` is read here and nowhere else
(CLAUDE.md section 5.4). No other module calls `os.environ`, so there is exactly
one place to look when a setting misbehaves and exactly one place to change when
a setting is added.

Parse failures are **collected, not raised one at a time**. A half-configured
droplet should fail on first boot with the complete list of what is wrong, not
force six deploy cycles to discover six missing variables.

Assumes: `.env` is loaded into the process environment before `Settings.load()`
runs — by systemd's `EnvironmentFile` in production, or by `load_dotenv()`
locally. `load_dotenv` is called for the local case and is a no-op when the file
is absent, which is the production case.

Does not handle: validating that a credential actually works. A syntactically
present token can still be revoked; that surfaces at the first API call, and
`ARCHITECTURE.md` section 6 says what happens then. Nor does it read the
service-account JSON — it checks the path exists and leaves the contents to the
Sheets client, because this module must never hold key material it could log.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeVar

from dotenv import load_dotenv

from gable.pipeline.schedule import PollSchedule

# PEP 695 (`def f[T](...)`) is 3.12+. CLAUDE.md section 9 sets 3.11 as the
# floor and the droplet's distro Python sets the ceiling, so TypeVar it is.
_Numeric = TypeVar("_Numeric", int, float)
_EnumT = TypeVar("_EnumT", bound=StrEnum)


class PhotoPolicy(StrEnum):
    """What Gable is permitted to do when no real photo can be retrieved.

    Chase has not settled this (CLAUDE.md section 8). It is configuration, not
    a code path chosen at build time, and all four values are honored faithfully.
    """

    RETRIEVE_ONLY = "retrieve_only"
    GENERATE_WITH_APPROVAL = "generate_with_approval"
    GENERATE_FREELY = "generate_freely"
    NO_AI = "no_ai"

    @property
    def allows_generation(self) -> bool:
        """True if this policy ever permits synthesizing a house photo."""
        return self in {PhotoPolicy.GENERATE_WITH_APPROVAL, PhotoPolicy.GENERATE_FREELY}

    @property
    def requires_approval_before_generating(self) -> bool:
        """True if Carmen must approve in Slack before a photo is generated."""
        return self is PhotoPolicy.GENERATE_WITH_APPROVAL

    @property
    def allows_reprocessing(self) -> bool:
        """True if a *real* photo may be reshaped to fit the template frame.

        Reprocessing changes framing, exposure and resolution; generation
        invents a subject. They are different operations on separate code
        paths. Every policy except `no_ai` permits reprocessing.
        """
        return self is not PhotoPolicy.NO_AI


class ConfigError(Exception):
    """Raised when the environment cannot produce a valid `Settings`.

    Carries every problem found, not just the first, so one boot reveals the
    full list.
    """

    def __init__(self, problems: list[str]) -> None:
        """Build the error from the collected problem descriptions."""
        self.problems = problems
        joined = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"{len(problems)} configuration problem(s):\n{joined}")


#: The one channel Gable may post to (CLAUDE.md section 11). Overridable by
#: environment so tests and a future second workspace are not blocked, but the
#: default is pinned so a missing variable cannot silently retarget Gable.
DEFAULT_SLACK_CHANNEL_ID: Final[str] = "C0BP597644B"

#: Ceilings that exist to stop an agent spending money or memory in a loop
#: (AGENTS.md section 7). Enforced in code, not in a comment.
MAX_ALLOWED_BATCH: Final[int] = 200
MIN_POLL_INTERVAL_SECONDS: Final[int] = 30


@dataclass(frozen=True, slots=True)
class Settings:
    """Every configuration value Gable reads. Immutable once built."""

    # --- Slack ---
    slack_bot_token: str
    slack_app_token: str
    slack_signing_secret: str
    slack_channel_id: str

    # --- Google ---
    google_service_account_file: Path
    sheet_id: str
    tab_responses: str
    tab_agents: str
    tab_runs: str
    tab_templates: str

    # --- Google Drive ---
    # A Shared Drive, never a My Drive folder: service accounts have a 0 GB
    # storage quota, so files they create anywhere else fail with
    # StorageQuotaExceeded.
    drive_id: str
    drive_templates_folder_id: str
    drive_output_folder_id: str

    # --- Rendering (Google Slides) ---
    slide_width_px: int
    slide_height_px: int
    max_image_url_bytes: int

    # --- Firecrawl ---
    firecrawl_api_key: str
    verify_cache_hours: int

    # --- Photo policy ---
    photo_policy: PhotoPolicy
    photo_min_confidence: float
    photo_reprocess: bool
    max_image_calls_per_listing: int

    # --- Photo hosting ---
    spaces_key: str
    spaces_secret: str
    spaces_region: str
    spaces_bucket: str
    spaces_public_base: str
    photo_max_edge_px: int
    photo_jpeg_quality: int

    # --- Pipeline ---
    poll_interval_seconds: int
    poll_busy_interval_seconds: int
    max_batch: int
    max_description_chars: int
    max_retries: int
    dry_run: bool

    # --- AI providers ---
    openai_image_api_key: str
    image_model: str
    anthropic_api_key: str
    anthropic_model: str

    # --- Logging ---
    log_level: str
    log_format: str
    log_redact_secrets: bool

    @property
    def images_available(self) -> bool:
        """True when an image model can actually be called."""
        return bool(self.openai_image_api_key)

    @property
    def generation_available(self) -> bool:
        """True only if policy permits generation AND something can generate.

        Both halves matter. Gable must never imply to Carmen that it could have
        produced a photo when no key is configured (AGENTS.md section 5).
        """
        return self.photo_policy.allows_generation and self.images_available

    @property
    def reprocessing_enabled(self) -> bool:
        """True if a real photo may be reshaped to fit the template frame.

        Policy is authoritative and the flag is subordinate: `no_ai` disables
        reprocessing regardless of `GABLE_PHOTO_REPROCESS`, rather than forcing
        the operator to edit two variables to express one intent.
        """
        return (
            self.photo_reprocess and self.photo_policy.allows_reprocessing and self.images_available
        )

    @property
    def poll_schedule(self) -> PollSchedule:
        """The two-rate poll schedule described in ARCHITECTURE.md 2.6.

        `GABLE_POLL_INTERVAL_SECONDS` is the quiet rate — nights and weekends —
        so a single unchanged variable still describes the slow path. The busy
        rate applies Mon-Fri 07:00-17:00 US Central.

        Returns:
            A frozen `PollSchedule`. Both intervals are already range-checked by
            `load`, so this cannot raise on a `Settings` that exists.

        Raises:
            Nothing.
        """
        return PollSchedule(
            busy_interval_seconds=self.poll_busy_interval_seconds,
            quiet_interval_seconds=self.poll_interval_seconds,
        )

    @classmethod
    def load(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        use_dotenv: bool = True,
        require_credentials: bool = True,
    ) -> Settings:
        """Parse settings from the environment.

        Args:
            environ: Environment mapping. Defaults to `os.environ`.
            use_dotenv: Load `.env` into `os.environ` first. Ignored when
                `environ` is supplied, so tests stay hermetic.
            require_credentials: When False, credential variables may be blank.
                This is how `cli.py` runs the pipeline with Slack absent
                (CLAUDE.md section 6) — not a way to skip validation in
                production.

        Returns:
            A frozen `Settings`.

        Raises:
            ConfigError: listing every problem found, if any value is missing,
                unparseable, or out of range.
        """
        if environ is None:
            if use_dotenv:
                # No-op in production, where systemd supplies the environment.
                load_dotenv(override=False)
            environ = os.environ

        problems: list[str] = []
        reader = _Reader(environ, problems)

        settings = cls(
            slack_bot_token=reader.secret("SLACK_BOT_TOKEN", require_credentials),
            slack_app_token=reader.secret("SLACK_APP_TOKEN", require_credentials),
            slack_signing_secret=reader.str_value("SLACK_SIGNING_SECRET", ""),
            slack_channel_id=reader.str_value("GABLE_SLACK_CHANNEL_ID", DEFAULT_SLACK_CHANNEL_ID),
            google_service_account_file=reader.path(
                "GOOGLE_SERVICE_ACCOUNT_FILE", must_exist=require_credentials
            ),
            sheet_id=reader.required("GABLE_SHEET_ID"),
            tab_responses=reader.str_value("GABLE_TAB_RESPONSES", "Form Responses 1"),
            tab_agents=reader.str_value("GABLE_TAB_AGENTS", "Agents"),
            tab_runs=reader.str_value("GABLE_TAB_RUNS", "Runs"),
            tab_templates=reader.str_value("GABLE_TAB_TEMPLATES", "Templates"),
            drive_id=reader.str_value("GABLE_DRIVE_ID", ""),
            drive_templates_folder_id=reader.str_value("GABLE_DRIVE_TEMPLATES_FOLDER_ID", ""),
            drive_output_folder_id=reader.str_value("GABLE_DRIVE_OUTPUT_FOLDER_ID", ""),
            slide_width_px=reader.int_value("GABLE_SLIDE_WIDTH_PX", 1080, minimum=100),
            slide_height_px=reader.int_value("GABLE_SLIDE_HEIGHT_PX", 1350, minimum=100),
            # Slides' documented cap. Lowering it is allowed; raising it past
            # 2048 would produce URLs Google rejects at insertion time.
            max_image_url_bytes=reader.int_value(
                "GABLE_MAX_IMAGE_URL_BYTES", 2048, minimum=256, maximum=2048
            ),
            firecrawl_api_key=reader.secret("FIRECRAWL_API_KEY", require_credentials),
            verify_cache_hours=reader.int_value("GABLE_VERIFY_CACHE_HOURS", 24, minimum=1),
            photo_policy=reader.enum_value(
                "GABLE_PHOTO_POLICY", PhotoPolicy, PhotoPolicy.GENERATE_WITH_APPROVAL
            ),
            photo_min_confidence=reader.float_value(
                "GABLE_PHOTO_MIN_CONFIDENCE", 0.75, minimum=0.0, maximum=1.0
            ),
            photo_reprocess=reader.bool_value("GABLE_PHOTO_REPROCESS", True),
            max_image_calls_per_listing=reader.int_value(
                "GABLE_MAX_IMAGE_CALLS_PER_LISTING", 1, minimum=0, maximum=5
            ),
            spaces_key=reader.str_value("SPACES_KEY", ""),
            spaces_secret=reader.str_value("SPACES_SECRET", ""),
            spaces_region=reader.str_value("SPACES_REGION", "nyc3"),
            spaces_bucket=reader.str_value("SPACES_BUCKET", "gable-photos"),
            spaces_public_base=reader.str_value("SPACES_PUBLIC_BASE", ""),
            photo_max_edge_px=reader.int_value(
                "GABLE_PHOTO_MAX_EDGE_PX", 2400, minimum=400, maximum=8000
            ),
            photo_jpeg_quality=reader.int_value(
                "GABLE_PHOTO_JPEG_QUALITY", 85, minimum=40, maximum=100
            ),
            poll_interval_seconds=reader.int_value(
                "GABLE_POLL_INTERVAL_SECONDS", 600, minimum=MIN_POLL_INTERVAL_SECONDS
            ),
            poll_busy_interval_seconds=reader.int_value(
                "GABLE_POLL_BUSY_INTERVAL_SECONDS", 120, minimum=MIN_POLL_INTERVAL_SECONDS
            ),
            max_batch=reader.int_value("GABLE_MAX_BATCH", 25, minimum=1, maximum=MAX_ALLOWED_BATCH),
            max_description_chars=reader.int_value(
                "GABLE_MAX_DESCRIPTION_CHARS", 400, minimum=40, maximum=10_000
            ),
            max_retries=reader.int_value("GABLE_MAX_RETRIES", 3, minimum=0, maximum=10),
            dry_run=reader.bool_value("GABLE_DRY_RUN", False),
            openai_image_api_key=reader.secret("OPENAI_IMAGE_API_KEY", False),
            image_model=reader.str_value("GABLE_IMAGE_MODEL", "gpt-image-2"),
            anthropic_api_key=reader.secret("ANTHROPIC_API_KEY", False),
            anthropic_model=reader.str_value("GABLE_ANTHROPIC_MODEL", "claude-opus-5"),
            log_level=reader.str_value("LOG_LEVEL", "INFO").upper(),
            log_format=reader.str_value("LOG_FORMAT", "json").lower(),
            log_redact_secrets=reader.bool_value("LOG_REDACT_SECRETS", True),
        )

        _validate_cross_field(settings, problems)
        if problems:
            raise ConfigError(problems)
        return settings


def _validate_cross_field(settings: Settings, problems: list[str]) -> None:
    """Check constraints that span more than one variable.

    The rule applied here: **fail only on configuration that is unsatisfiable,
    degrade safely otherwise.** A missing image provider does not stop Gable —
    it means the photo cascade ends at "ask Carmen" (CLAUDE.md section 8, step
    5), which is a safe outcome and the normal state of a Phase 1 deployment
    that never generates anything. Refusing to boot over it would make the
    shipped `.env.example` unstartable.

    The one genuinely unsatisfiable combination is `generate_freely` with no
    usable provider: the operator asked for automatic generation and there is
    nothing to generate with.
    """
    # Google Slides fetches an inserted image over the public internet and
    # requires HTTPS. A non-HTTPS base parses fine and then fails at render.
    if settings.spaces_public_base and not settings.spaces_public_base.startswith("https://"):
        problems.append("SPACES_PUBLIC_BASE must start with https:// — Slides requires HTTPS")

    # A My Drive folder id would parse fine and then fail on the first render
    # with StorageQuotaExceeded. Shared drive ids start with "0A"; folder ids
    # do not. ASSUMPTION: that prefix convention holds — settled the first time
    # the Drive client lists the drive, which reports its own type.
    if settings.drive_id and not settings.drive_id.startswith("0A"):
        problems.append(
            f"GABLE_DRIVE_ID={settings.drive_id!r} does not look like a shared drive id "
            "(expected to start with '0A'). Service accounts cannot create files "
            "outside a shared drive."
        )
    if settings.photo_policy is PhotoPolicy.GENERATE_FREELY and not settings.images_available:
        problems.append(
            "GABLE_PHOTO_POLICY=generate_freely requires OPENAI_IMAGE_API_KEY: "
            "set it, or choose a policy that does not generate automatically"
        )
    if settings.log_format not in {"json", "console"}:
        problems.append(f"LOG_FORMAT must be json or console, got {settings.log_format!r}")
    if settings.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        problems.append(f"LOG_LEVEL is not a known level: {settings.log_level!r}")
    if not settings.log_redact_secrets:
        # CLAUDE.md section 3 makes redaction a mechanism, not a preference.
        problems.append("LOG_REDACT_SECRETS=false is not permitted; secrets must never be logged")


class _Reader:
    """Reads and coerces one variable at a time, appending failures to a list.

    Every method returns a usable value even on failure, so parsing continues
    and one boot reports every problem instead of the first.
    """

    def __init__(self, environ: Mapping[str, str], problems: list[str]) -> None:
        """Bind the reader to an environment and a problem accumulator."""
        self._environ = environ
        self._problems = problems

    def _raw(self, name: str) -> str:
        return self._environ.get(name, "").strip()

    def str_value(self, name: str, default: str) -> str:
        """Read a string, falling back to `default` when unset or blank."""
        return self._raw(name) or default

    def required(self, name: str) -> str:
        """Read a string that must be present and non-blank."""
        value = self._raw(name)
        if not value:
            self._problems.append(f"{name} is required and is unset or blank")
        return value

    def secret(self, name: str, required: bool) -> str:
        """Read a credential, required only when `required` is True.

        The value is never echoed into a problem message — a truncated token in
        an error string is still a leaked token.
        """
        value = self._raw(name)
        if required and not value:
            self._problems.append(f"{name} is required and is unset or blank")
        return value

    def path(self, name: str, must_exist: bool) -> Path:
        """Read a filesystem path, optionally requiring that it exists."""
        raw = self._raw(name)
        if not raw:
            if must_exist:
                self._problems.append(f"{name} is required and is unset or blank")
            return Path()
        candidate = Path(raw).expanduser()
        if must_exist and not candidate.is_file():
            self._problems.append(f"{name}: no file at {candidate}")
        return candidate

    def int_value(
        self, name: str, default: int, *, minimum: int | None = None, maximum: int | None = None
    ) -> int:
        """Read a bounded integer."""
        raw = self._raw(name)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            self._problems.append(f"{name}: {raw!r} is not an integer")
            return default
        return self._bounded(name, value, default, minimum, maximum)

    def float_value(
        self,
        name: str,
        default: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        """Read a bounded float."""
        raw = self._raw(name)
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            self._problems.append(f"{name}: {raw!r} is not a number")
            return default
        return self._bounded(name, value, default, minimum, maximum)

    def _bounded(
        self,
        name: str,
        value: _Numeric,
        default: _Numeric,
        minimum: _Numeric | None,
        maximum: _Numeric | None,
    ) -> _Numeric:
        if minimum is not None and value < minimum:
            self._problems.append(f"{name}: {value} is below the minimum of {minimum}")
            return default
        if maximum is not None and value > maximum:
            self._problems.append(f"{name}: {value} is above the maximum of {maximum}")
            return default
        return value

    def bool_value(self, name: str, default: bool) -> bool:
        """Read a boolean.

        Only explicit spellings are accepted. A typo like `GABLE_DRY_RUN=ture`
        must not quietly evaluate falsey and start writing to a live Sheet.
        """
        raw = self._raw(name).lower()
        if not raw:
            return default
        if raw in {"true", "1", "yes", "on"}:
            return True
        if raw in {"false", "0", "no", "off"}:
            return False
        self._problems.append(f"{name}: {raw!r} is not a boolean (use true or false)")
        return default

    def enum_value(self, name: str, enum_type: type[_EnumT], default: _EnumT) -> _EnumT:
        """Read one of an enum's values, listing the valid options on failure."""
        raw = self._raw(name).lower()
        if not raw:
            return default
        try:
            return enum_type(raw)
        except ValueError:
            options = ", ".join(member.value for member in enum_type)
            self._problems.append(f"{name}: {raw!r} is not valid (expected one of: {options})")
            return default
