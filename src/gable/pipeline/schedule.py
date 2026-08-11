"""When to poll the intake Sheet.

Requests arrive during the working day, so Gable polls fast while Carmen and the
agents are working and slowly the rest of the time (ARCHITECTURE.md 2.6):

    Mon-Fri, 07:00-17:00 US Central   ->  every 2 minutes
    everything else                   ->  every 10 minutes

Everything here is pure. The caller supplies the instant; nothing in this module
reads a clock, which is what makes the daylight-saving behaviour testable rather
than something that surfaces in March.

What this module does NOT handle: holidays (there is no holiday calendar, and
the cost of polling on Thanksgiving is one wasted Sheets read every two minutes),
and per-agent timezones (the operating timezone is Central, full stop).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The operating timezone. Named rather than a fixed offset so the US daylight
# saving transitions are handled by the tz database instead of by us.
OPERATING_TIMEZONE_NAME: Final[str] = "America/Chicago"

BUSINESS_START: Final[time] = time(7, 0)
BUSINESS_END: Final[time] = time(17, 0)

# Monday is 0 in datetime.weekday(); Saturday and Sunday are 5 and 6.
_FIRST_WEEKEND_DAY: Final[int] = 5

BUSY_INTERVAL_SECONDS: Final[int] = 120
QUIET_INTERVAL_SECONDS: Final[int] = 600


def operating_timezone() -> tzinfo:
    """Return the America/Chicago zone, or UTC if no tz database is present.

    Returns:
        A `tzinfo`. `ZoneInfo("America/Chicago")` on any normal system.

    Raises:
        Nothing. A missing tz database degrades to UTC rather than refusing to
        start: polling on the wrong schedule is a minor inefficiency, while a
        crash-on-boot over a timezone lookup takes the whole product down.

    ASSUMPTION: the droplet has a system tz database. Ubuntu ships one, and
    `python3-tzdata` covers the case where it does not. Confirmed by running
    this function on the droplet — until then the fallback is why it is safe to
    be wrong.
    """
    try:
        return ZoneInfo(OPERATING_TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        return UTC


def is_business_hours(moment: datetime, zone: tzinfo | None = None) -> bool:
    """True if `moment` falls inside the Central-time business window.

    Args:
        moment: The instant to test. A naive datetime is treated as UTC, which
            matches every other timestamp in this codebase (models.to_iso_utc).
        zone: The zone defining the window. Defaults to `operating_timezone()`.

    Returns:
        True on Mon-Fri between 07:00 and 17:00 in `zone`. The window is
        half-open: 07:00:00 is inside it, 17:00:00 is not.

    Raises:
        Nothing.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    local = moment.astimezone(zone if zone is not None else operating_timezone())

    if local.weekday() >= _FIRST_WEEKEND_DAY:
        return False

    # Half-open so that a schedule flipping to quiet at exactly 17:00 does not
    # depend on whether the clock ticked before or after the comparison.
    return BUSINESS_START <= local.time() < BUSINESS_END


@dataclass(frozen=True, slots=True)
class PollSchedule:
    """The two poll intervals, and the rule for choosing between them.

    Frozen because the schedule is configuration, resolved once at startup.
    """

    busy_interval_seconds: int = BUSY_INTERVAL_SECONDS
    quiet_interval_seconds: int = QUIET_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        """Reject a schedule that cannot describe a working poller.

        Raises:
            ValueError: If either interval is not positive. A zero or negative
                interval is a busy loop against Google's quota, not a fast
                poller.
        """
        if self.busy_interval_seconds <= 0 or self.quiet_interval_seconds <= 0:
            msg = (
                "poll intervals must be positive, got "
                f"busy={self.busy_interval_seconds}s quiet={self.quiet_interval_seconds}s"
            )
            raise ValueError(msg)

    def interval_seconds(self, moment: datetime, zone: tzinfo | None = None) -> int:
        """Seconds to wait before the next poll, given the current instant.

        Args:
            moment: Now, as the caller sees it. Naive means UTC.
            zone: Override for the business-hours zone. Tests use this; the
                running poller does not.

        Returns:
            `busy_interval_seconds` during the business window, else
            `quiet_interval_seconds`.

        Raises:
            Nothing.
        """
        if is_business_hours(moment, zone):
            return self.busy_interval_seconds
        return self.quiet_interval_seconds

    def describe(self, moment: datetime, zone: tzinfo | None = None) -> str:
        """A one-line explanation for `/gable status` and the startup log.

        Args:
            moment: The instant to describe.
            zone: Override for the business-hours zone.

        Returns:
            Something like `every 2m (business hours)`. Written for a human
            reading Slack, not for a log parser.

        Raises:
            Nothing.
        """
        busy = is_business_hours(moment, zone)
        seconds = self.interval_seconds(moment, zone)
        window = "business hours" if busy else "outside business hours"
        return f"every {_humanize_seconds(seconds)} ({window})"


def _humanize_seconds(seconds: int) -> str:
    """Render a poll interval the way a person would say it.

    Args:
        seconds: A positive interval.

    Returns:
        `120` -> `2m`, `90` -> `90s`, `600` -> `10m`. Falls back to seconds
        whenever the value does not divide evenly, because `1.5m` reads worse
        than `90s`.

    Raises:
        Nothing.
    """
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"
