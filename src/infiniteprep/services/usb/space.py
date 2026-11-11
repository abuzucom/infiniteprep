"""Conservative space estimate and reserve for USB export (D96 to D99, D180, D199)."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

# FAT32 clusters never exceed 64 KiB. Rounding every file to that size keeps the
# estimate an upper bound for any volume without reading the real cluster size.
BYTES_PER_KIB: Final[int] = 1024
FAT32_MAX_CLUSTER_KIB: Final[int] = 64
UPPER_BOUND_CLUSTER_BYTES: Final[int] = FAT32_MAX_CLUSTER_KIB * BYTES_PER_KIB
PERCENT_DIVISOR: Final[int] = 100


@dataclass(frozen=True)
class ReservePolicy:
    """Extra free space kept beyond the estimate. Both kinds start at zero (D99)."""

    percent: float = 0.0
    fixed_bytes: int = 0


def _require_non_negative(value: int, name: str) -> None:
    """Raise ValueError when a byte or item count is negative."""
    if value < 0:
        raise ValueError(f"{name} must not be negative. Supply a value of zero or more.")


def estimate_export_bytes(
    file_sizes: Iterable[int],
    directory_count: int = 0,
    database_allowance_bytes: int = 0,
) -> int:
    """Return an upper bound of the bytes an export consumes on a FAT32 volume.

    Each file takes its size rounded up to whole clusters plus one cluster for
    directory entries. Each directory takes one cluster.
    """
    _require_non_negative(directory_count, "directory_count")
    _require_non_negative(database_allowance_bytes, "database_allowance_bytes")
    cluster_count = directory_count
    for size in file_sizes:
        _require_non_negative(size, "file size")
        cluster_count += -(-size // UPPER_BOUND_CLUSTER_BYTES) + 1
    return cluster_count * UPPER_BOUND_CLUSTER_BYTES + database_allowance_bytes


def compute_reserve_bytes(estimate_bytes: int, reserve: ReservePolicy) -> int:
    """Return the larger of the percentage reserve and the fixed reserve (D199).

    The percentage applies to the estimated export size and rounds up.
    """
    _require_non_negative(estimate_bytes, "estimate_bytes")
    _require_non_negative(reserve.fixed_bytes, "fixed_bytes")
    if not math.isfinite(reserve.percent) or reserve.percent < 0:
        raise ValueError("Reserve percent must be a finite value of zero or more.")
    percent_bytes = math.ceil(estimate_bytes * reserve.percent / PERCENT_DIVISOR)
    return max(percent_bytes, reserve.fixed_bytes)
