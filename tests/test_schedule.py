"""Tests for the polling schedule.

The interesting cases are all boundaries: the edges of the window, the edges of
the week, and the two daylight-saving transitions. Every instant here is built
explicitly in a named zone, so a failure names the hour it broke on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from gable.pipeline.schedule import (
    BUSY_INTERVAL_SECONDS,
    QUIET_INTERVAL_SECONDS,
    PollSchedule,
    is_business_hours,
    operating_timezone,
)

CENTRAL = ZoneInfo("America/Chicago")


def _central(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build an aware instant in the operating timezone."""
    return datetime(year, month, day, hour, minute, tzinfo=CENTRAL)


# --- the window -------------------------------------------------------------


@pytest.mark.parametrize("hour", [7, 8, 12, 16])
def test_weekday_working_hours_are_busy(hour: int) -> None:
    # 2026-08-10 is a Monday.
    assert is_business_hours(_central(2026, 8, 10, hour)) is True


@pytest.mark.parametrize("hour", [0, 3, 6, 17, 18, 23])
def test_weekday_off_hours_are_quiet(hour: int) -> None:
    assert is_business_hours(_central(2026, 8, 10, hour)) is False


def test_window_opens_exactly_at_seven() -> None:
    assert is_business_hours(_central(2026, 8, 10, 7, 0)) is True
    assert is_business_hours(_central(2026, 8, 10, 6, 59)) is False


def test_window_closes_exactly_at_five() -> None:
    """Half-open: 17:00:00 is already quiet."""
    assert is_business_hours(_central(2026, 8, 10, 16, 59)) is True
    assert is_business_hours(_central(2026, 8, 10, 17, 0)) is False


def test_a_request_at_four_fifty_eight_friday_is_still_busy() -> None:
    """The case ARCHITECTURE.md 2.6 calls out by name. 2026-08-14 is a Friday."""
    assert is_business_hours(_central(2026, 8, 14, 16, 58)) is True


# --- the week ---------------------------------------------------------------


@pytest.mark.parametrize("day", [15, 16])  # Saturday, Sunday
def test_weekends_are_quiet_even_at_noon(day: int) -> None:
    assert is_business_hours(_central(2026, 8, day, 12)) is False


def test_monday_morning_is_busy_again() -> None:
    assert is_business_hours(_central(2026, 8, 17, 9)) is True


# --- timezone correctness ---------------------------------------------------


def test_utc_input_is_converted_not_compared_raw() -> None:
    """15:00 UTC is 10:00 Central in summer — busy, despite looking late."""
    assert is_business_hours(datetime(2026, 8, 10, 15, 0, tzinfo=UTC)) is True


def test_naive_input_is_treated_as_utc() -> None:
    """Matches models.to_iso_utc. 15:00 naive == 15:00 UTC == 10:00 Central."""
    assert is_business_hours(datetime(2026, 8, 10, 15, 0)) is True


def test_midnight_utc_is_the_previous_evening_in_central() -> None:
    """00:00 UTC Tuesday is 19:00 Monday Central: quiet, and still a weekday."""
    assert is_business_hours(datetime(2026, 8, 11, 0, 0, tzinfo=UTC)) is False


def test_saturday_utc_can_be_friday_afternoon_in_central() -> None:
    """The weekday check must run on the CONVERTED instant, not the input.

    2026-08-15 01:00 UTC is Saturday in UTC and 20:00 Friday in Central. Both
    readings agree on quiet here, so the sharper case is the reverse below.
    """
    assert is_business_hours(datetime(2026, 8, 15, 1, 0, tzinfo=UTC)) is False


def test_monday_early_utc_is_still_sunday_in_central() -> None:
    """A UTC-only weekday check would get this one wrong.

    04:00 UTC Monday is 23:00 Sunday Central: a weekday in UTC, and correctly
    the weekend once converted.
    """
    assert is_business_hours(datetime(2026, 8, 17, 4, 0, tzinfo=UTC)) is False


def test_summer_and_winter_use_the_same_wall_clock_window() -> None:
    """The March/November bug: 09:00 Central is busy in both CST and CDT.

    In UTC these are 15:00 and 14:00 respectively. Anything comparing raw UTC
    hours passes one of these and fails the other.
    """
    assert is_business_hours(_central(2026, 7, 15, 9)) is True  # CDT, UTC-5
    assert is_business_hours(_central(2026, 1, 15, 9)) is True  # CST, UTC-6


def test_operating_timezone_is_central() -> None:
    assert operating_timezone() == CENTRAL


# --- the schedule itself ----------------------------------------------------


def test_default_intervals_match_the_documented_schedule() -> None:
    schedule = PollSchedule()
    assert schedule.busy_interval_seconds == BUSY_INTERVAL_SECONDS == 120
    assert schedule.quiet_interval_seconds == QUIET_INTERVAL_SECONDS == 600


def test_interval_switches_with_the_window() -> None:
    schedule = PollSchedule()
    assert schedule.interval_seconds(_central(2026, 8, 10, 9)) == 120
    assert schedule.interval_seconds(_central(2026, 8, 10, 22)) == 600


def test_custom_intervals_are_honored() -> None:
    schedule = PollSchedule(busy_interval_seconds=60, quiet_interval_seconds=900)
    assert schedule.interval_seconds(_central(2026, 8, 10, 9)) == 60
    assert schedule.interval_seconds(_central(2026, 8, 15, 9)) == 900


@pytest.mark.parametrize(
    ("busy", "quiet"),
    [(0, 600), (120, 0), (-1, 600), (120, -600)],
)
def test_non_positive_intervals_are_rejected(busy: int, quiet: int) -> None:
    """A zero interval is a busy loop against Google's quota."""
    with pytest.raises(ValueError, match="must be positive"):
        PollSchedule(busy_interval_seconds=busy, quiet_interval_seconds=quiet)


def test_schedule_is_frozen() -> None:
    schedule = PollSchedule()
    with pytest.raises(AttributeError):
        schedule.busy_interval_seconds = 1  # type: ignore[misc]


# --- the human-readable description -----------------------------------------


def test_describe_names_the_window_and_the_rate() -> None:
    schedule = PollSchedule()
    assert schedule.describe(_central(2026, 8, 10, 9)) == "every 2m (business hours)"


def test_describe_says_when_it_is_quiet() -> None:
    schedule = PollSchedule()
    assert schedule.describe(_central(2026, 8, 15, 9)) == "every 10m (outside business hours)"


def test_describe_falls_back_to_seconds_when_minutes_would_be_fractional() -> None:
    """`90s` reads better than `1.5m`."""
    schedule = PollSchedule(busy_interval_seconds=90, quiet_interval_seconds=45)
    assert "90s" in schedule.describe(_central(2026, 8, 10, 9))
    assert "45s" in schedule.describe(_central(2026, 8, 15, 9))
