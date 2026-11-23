"""Plain texts and values for the two-step USB export dialog.

Drive labels, models, and serials come from the drive and are untrusted. The
dialog renders every value these helpers return as plain text.
"""

from __future__ import annotations

from typing import Final

from infiniteprep.services.usb.models import DriveInspection
from infiniteprep.services.usb.preflight import (
    FACT_ACTIVE_PARTITION,
    FACT_FILESYSTEM_HEALTH,
    FACT_PRIMARY_PARTITION,
    FACT_SYSTEM_VOLUME,
    PreflightRequest,
    PreflightResult,
)

# Drive makers print decimal sizes on the box, so the dialog uses them too.
BYTES_PER_DECIMAL_STEP: Final[int] = 1000
DECIMAL_UNITS: Final[tuple[str, ...]] = ("KB", "MB", "GB", "TB")
SPACE_BAR_STEPS: Final[int] = 1000
UNKNOWN_FREE_TEXT: Final[str] = "This drive doesn't report its free space."
NO_DRIVE_CHANGES_TEXT: Final[str] = "Nothing on the drive changes."
NOT_AVAILABLE_TEXT: Final[str] = "Not available"
DETAIL_LOCATION: Final[str] = "Location"
DETAIL_MODEL: Final[str] = "Model"
DETAIL_DEVICE_ID: Final[str] = "Device ID"
DETAIL_SERIAL: Final[str] = "Serial number"
DETAIL_UNCHECKED: Final[str] = "Couldn't check"
DETAIL_SEPARATOR: Final[str] = ", "
FACT_PLAIN_NAMES: Final[dict[str, str]] = {
    FACT_ACTIVE_PARTITION: "Active partition",
    FACT_FILESYSTEM_HEALTH: "Drive health",
    FACT_PRIMARY_PARTITION: "Partition layout",
    FACT_SYSTEM_VOLUME: "System drive",
}


def format_size(byte_count: int) -> str:
    """Return a size such as "16.0 GB", or a byte count below one kilobyte."""
    if byte_count < BYTES_PER_DECIMAL_STEP:
        return f"{byte_count} bytes"
    value = float(byte_count)
    for unit in DECIMAL_UNITS[:-1]:
        value /= BYTES_PER_DECIMAL_STEP
        if value < BYTES_PER_DECIMAL_STEP:
            return f"{value:.1f} {unit}"
    value /= BYTES_PER_DECIMAL_STEP
    return f"{value:.1f} {DECIMAL_UNITS[-1]}"


def needed_bytes(result: PreflightResult) -> int:
    """Return the space the export needs: the estimate plus the reserve."""
    return result.estimate_bytes + result.reserve_bytes


def space_summary(result: PreflightResult) -> str:
    """Return a line such as "1.2 GB needed of 16.0 GB free"."""
    if result.available_bytes is None:
        return UNKNOWN_FREE_TEXT
    needed = format_size(needed_bytes(result))
    return f"{needed} needed of {format_size(result.available_bytes)} free"


def space_bar_value(needed: int, free: int | None) -> int | None:
    """Return the bar fill out of SPACE_BAR_STEPS, or None when free space is unknown.

    Byte counts pass the range of a Qt progress bar, so the bar shows a share.
    """
    if free is None:
        return None
    if free <= 0:
        return SPACE_BAR_STEPS
    return min(SPACE_BAR_STEPS, round(needed * SPACE_BAR_STEPS / free))


def drive_title(inspection: DriveInspection) -> str:
    """Return the name the drive shows: its volume name, model, or device name."""
    partition = inspection.partitions[0] if inspection.partitions else None
    label = partition.label if partition else None
    return label or inspection.drive.model or inspection.drive.name


def detail_rows(inspection: DriveInspection, result: PreflightResult) -> list[tuple[str, str]]:
    """Return the identifier rows that sit behind the Drive details toggle."""
    partition = inspection.partitions[0] if inspection.partitions else None
    values = (
        (DETAIL_LOCATION, result.mount_path),
        (DETAIL_MODEL, inspection.drive.model),
        (DETAIL_DEVICE_ID, inspection.drive.hardware_id),
        (DETAIL_SERIAL, partition.uuid if partition else None),
    )
    rows = [(name, value or NOT_AVAILABLE_TEXT) for name, value in values]
    if result.unverified_facts:
        unchecked = [FACT_PLAIN_NAMES.get(fact, fact) for fact in result.unverified_facts]
        rows.append((DETAIL_UNCHECKED, DETAIL_SEPARATOR.join(unchecked)))
    return rows


def drive_change_lines(request: PreflightRequest) -> list[str]:
    """Return what the export does on the drive, or one sentence that nothing changes."""
    return list(request.planned_changes) or [NO_DRIVE_CHANGES_TEXT]
