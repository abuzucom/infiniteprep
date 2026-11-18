"""Shared time decomposition for track durations and player clocks."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Final

SECONDS_PER_MINUTE: Final[int] = 60
MINUTES_PER_HOUR: Final[int] = 60
LOCAL_TIMESTAMP_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


def format_local_timestamp(stored_text: str, zone: tzinfo | None = None) -> str:
    """Return a stored UTC timestamp as local date and time text for display.

    zone defaults to the zone of this computer. Text that names no instant comes back
    unchanged, so a damaged value stays visible in the table.
    """
    try:
        parsed = datetime.fromisoformat(stored_text)
    except ValueError:
        return stored_text
    if parsed.utcoffset() is None:
        return stored_text
    try:
        local = parsed.astimezone(zone)
    except OverflowError:
        # A date at the edge of the supported range has no local form in some zones.
        return stored_text
    return local.strftime(LOCAL_TIMESTAMP_FORMAT)


def split_time_seconds(total_seconds: int) -> tuple[int, int, int]:
    """Return hours, minutes, and seconds for a nonnegative whole-second value."""
    total_minutes, seconds = divmod(total_seconds, SECONDS_PER_MINUTE)
    hours, minutes = divmod(total_minutes, MINUTES_PER_HOUR)
    return hours, minutes, seconds
