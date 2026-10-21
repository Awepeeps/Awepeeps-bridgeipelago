"""Daily broadcasts of the existing death count and check graph outputs."""

import asyncio
import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIME = "00:00"
DEFAULT_TIMEZONE = "America/New_York"
UTC = dt.timezone.utc
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class DailyPostConfigError(ValueError):
    """Raised when DailyPostConfig exists but cannot be used."""


@dataclass(frozen=True)
class DailyPostSettings:
    hour: int
    minute: int
    timezone: ZoneInfo
    timezone_name: str

    @property
    def wall_time(self):
        return dt.time(self.hour, self.minute)


def load_daily_post_settings(raw_config=None):
    """Return validated settings, or None when daily posting is disabled."""
    config = raw_config if raw_config is not None else {}
    try:
        enabled = config.get("Enabled", False)
    except AttributeError:
        raise DailyPostConfigError("DailyPostConfig must be an object.") from None

    if not isinstance(enabled, bool):
        raise DailyPostConfigError(
            "DailyPostConfig.Enabled must be true or false, got "
            + repr(enabled)
            + "."
        )
    if not enabled:
        return None

    time_value = config.get("Time", DEFAULT_TIME)
    if not isinstance(time_value, str):
        raise DailyPostConfigError(
            "DailyPostConfig.Time must be a 24-hour HH:MM string, got "
            + repr(time_value)
            + "."
        )
    match = _TIME_PATTERN.match(time_value)
    if match is None:
        raise DailyPostConfigError(
            "DailyPostConfig.Time must be a 24-hour HH:MM time between "
            "00:00 and 23:59, got " + repr(time_value) + "."
        )
    hour, minute = int(match.group(1)), int(match.group(2))

    timezone_name = config.get("Timezone", DEFAULT_TIMEZONE)
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise DailyPostConfigError(
            "DailyPostConfig.Timezone must be an IANA timezone name such as "
            "America/New_York, got " + repr(timezone_name) + "."
        )
    timezone_name = timezone_name.strip()
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise DailyPostConfigError(
            "DailyPostConfig.Timezone is not a valid IANA timezone: "
            + repr(timezone_name)
            + "."
        ) from None

    return DailyPostSettings(hour, minute, timezone, timezone_name)


def _local_candidate(day, wall_time, timezone):
    """Resolve a wall time on a local day, skipping times erased by DST."""
    naive = dt.datetime.combine(day, wall_time)
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=timezone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(timezone)
        if round_trip.replace(tzinfo=None) == naive:
            # fold=0 is the first occurrence when a fall-back hour repeats.
            return candidate
    return None


def next_run_after(now, settings):
    """Return the next configured local wall time strictly after now (UTC)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(UTC)
    local_day = now_utc.astimezone(settings.timezone).date()
    for offset in range(4):
        candidate = _local_candidate(
            local_day + dt.timedelta(days=offset),
            settings.wall_time,
            settings.timezone,
        )
        if candidate is None:
            continue
        candidate_utc = candidate.astimezone(UTC)
        if candidate_utc > now_utc:
            return candidate_utc
    raise DailyPostConfigError("Unable to resolve the next daily post time.")


class DailyPostState:
    """Remembers the last local date posted for one Archipelago session."""

    def __init__(self, path, log=None):
        self.path = path
        self.last_post_date = None
        self._log = log if log is not None else (lambda message: None)
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            self._log("Could not read daily post state: " + str(error))
            return

        value = data.get("LastPostDate") if isinstance(data, dict) else None
        try:
            self.last_post_date = dt.date.fromisoformat(value)
        except (TypeError, ValueError):
            self._log("Ignoring invalid daily post state date: " + repr(value))

    def already_posted(self, local_date):
        return self.last_post_date == local_date

    def mark_posted(self, local_date):
        # Update memory first so a failed disk write cannot duplicate this run.
        self.last_post_date = local_date
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as state_file:
            json.dump({"LastPostDate": local_date.isoformat()}, state_file)
        os.replace(temporary, self.path)


async def run_daily_posts(settings, state, post, now=None, sleep=asyncio.sleep,
                          on_error=None):
    """Wait for each configured local time and post at most once per local day.

    Startup never catches up: the first post is always the next configured
    wall time after the loop begins.
    """
    now = now if now is not None else (lambda: dt.datetime.now(UTC))
    while True:
        next_run = next_run_after(now(), settings)
        delay = max(0.0, (next_run - now()).total_seconds())
        await sleep(delay)

        # A short sleep can wake a moment early; recompute on the next pass.
        if now() < next_run:
            continue

        # The post belongs to the day it was scheduled for, even if it runs
        # just after midnight or the process was suspended across the time.
        local_date = next_run.astimezone(settings.timezone).date()
        if state.already_posted(local_date):
            continue

        try:
            await post()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if on_error is not None:
                on_error(error)
            continue

        try:
            state.mark_posted(local_date)
        except OSError as error:
            if on_error is not None:
                on_error(error)
