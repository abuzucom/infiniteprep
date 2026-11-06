"""Shared time decomposition for track durations and player clocks."""

from __future__ import annotations

from typing import Final

SECONDS_PER_MINUTE: Final[int] = 60


def split_time_seconds(total_seconds: int) -> tuple[int, int, int]:
    """Return hours, minutes, and seconds for a nonnegative whole-second value."""
    total_minutes, seconds = divmod(total_seconds, SECONDS_PER_MINUTE)
    hours, minutes = divmod(total_minutes, SECONDS_PER_MINUTE)
    return hours, minutes, seconds
