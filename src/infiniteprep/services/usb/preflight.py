"""USB export preflight policy (D94, D96, D131, D137, D138, D184 to D189, D202).

The policy reads an inspection and an export request. It never touches the drive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from infiniteprep.services.usb.models import (
    DriveIdentity,
    DriveInspection,
    FilesystemHealth,
    PartitionInfo,
)
from infiniteprep.services.usb.space import (
    ReservePolicy,
    compute_reserve_bytes,
    estimate_export_bytes,
)

# FAT32 stores a file size in 32 bits. The largest file holds 4 GiB minus one byte.
# A file of 4 GiB or more is blocked.
FAT32_FILE_SIZE_BITS: Final[int] = 32
FAT32_BLOCKED_FILE_BYTES: Final[int] = 2**FAT32_FILE_SIZE_BITS
REQUIRED_PARTITION_COUNT: Final[int] = 1
REMOVABLE_BUS_TYPES: Final[frozenset[str]] = frozenset({"usb", "sd", "mmc", "secure digital"})
MBR_SCHEME_NAMES: Final[frozenset[str]] = frozenset(
    {"dos", "mbr", "msdos", "fdisk_partition_scheme"}
)

FACT_ACTIVE_PARTITION: Final[str] = "active_partition"
FACT_FILESYSTEM_HEALTH: Final[str] = "filesystem_health"
FACT_PRIMARY_PARTITION: Final[str] = "primary_partition"
FACT_SYSTEM_VOLUME: Final[str] = "system_volume"

REPAIR_GUIDANCE: Final[str] = (
    "Repair the filesystem with the operating system disk tool, then retry. "
    "A drive mounted read-only on purpose gives the same result. "
    "Mount such a drive writable, then retry. "
    "InfinitePrep never repairs or remounts a drive."
)
READ_ONLY_GUIDANCE: Final[str] = (
    "Release the write-protect switch or choose another drive. "
    "InfinitePrep never changes mount state or permissions."
)


class RefusalCode(StrEnum):
    """Stable identifiers for every preflight refusal."""

    NOT_REMOVABLE = "not_removable"
    SYSTEM_VOLUME = "system_volume"
    SYSTEM_STATE_UNKNOWN = "system_state_unknown"
    ACTIVE_PARTITION = "active_partition"
    READ_ONLY_MEDIA = "read_only_media"
    FILESYSTEM_ERRORS = "filesystem_errors"
    UNKNOWN_PARTITION_LAYOUT = "unknown_partition_layout"
    UNSUPPORTED_PARTITION_SCHEME = "unsupported_partition_scheme"
    PARTITION_COUNT = "partition_count"
    NOT_PRIMARY_PARTITION = "not_primary_partition"
    UNKNOWN_FILESYSTEM = "unknown_filesystem"
    UNSUPPORTED_FILESYSTEM = "unsupported_filesystem"
    NOT_MOUNTED = "not_mounted"
    UNKNOWN_DATABASE_FORMAT = "unknown_database_format"
    FILE_TOO_LARGE = "file_too_large"
    SPACE_UNKNOWN = "space_unknown"
    INSUFFICIENT_SPACE = "insufficient_space"


# A safety refusal returns alone. Details of a drive that must never receive
# an export stay hidden.
SAFETY_REFUSAL_CODES: Final[frozenset[RefusalCode]] = frozenset(
    {
        RefusalCode.NOT_REMOVABLE,
        RefusalCode.SYSTEM_VOLUME,
        RefusalCode.SYSTEM_STATE_UNKNOWN,
        RefusalCode.ACTIVE_PARTITION,
    }
)


@dataclass(frozen=True)
class Refusal:
    """One reason the preflight refuses a target."""

    code: RefusalCode
    message: str
    guidance: str = ""


@dataclass(frozen=True)
class DjProfileRequest:
    """One DJ profile of an export. None support means the format is unknown."""

    name: str
    database_format_supported: bool | None


@dataclass(frozen=True)
class PreflightRequest:
    """The planned export that the preflight evaluates."""

    profiles: tuple[DjProfileRequest, ...]
    file_sizes: tuple[int, ...]
    directory_count: int = 0
    database_allowance_bytes: int = 0
    planned_changes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of one preflight evaluation."""

    refusals: tuple[Refusal, ...]
    unverified_facts: tuple[str, ...]
    estimate_bytes: int
    reserve_bytes: int
    available_bytes: int | None
    mount_path: str | None

    @property
    def passed(self) -> bool:
        """Return True when no rule refuses the target."""
        return not self.refusals


def _select_single_partition(inspection: DriveInspection) -> PartitionInfo | None:
    """Return the only partition, or None for any other partition count."""
    if len(inspection.partitions) != REQUIRED_PARTITION_COUNT:
        return None
    return inspection.partitions[0]


def is_removable_class(drive: DriveIdentity) -> bool:
    """Return True for a drive with the removable flag on a USB, SD, or MMC bus (D188)."""
    bus_type = (drive.bus_type or "").casefold()
    return drive.is_removable and bus_type in REMOVABLE_BUS_TYPES


def _collect_safety_refusals(inspection: DriveInspection) -> list[Refusal]:
    """Return refusals for non-removable, system, boot, and active targets."""
    drive = inspection.drive
    refusals: list[Refusal] = []
    if not is_removable_class(drive):
        refusals.append(
            Refusal(
                RefusalCode.NOT_REMOVABLE,
                "The drive is not a removable USB or card drive.",
                "Choose a removable USB stick or memory card.",
            )
        )
    if drive.is_system:
        refusals.append(
            Refusal(RefusalCode.SYSTEM_VOLUME, "The drive holds a system or boot volume.")
        )
    elif drive.is_system is None:
        refusals.append(
            Refusal(
                RefusalCode.SYSTEM_STATE_UNKNOWN,
                "The system and boot state of the drive is unknown.",
            )
        )
    # A probe without raw device access cannot read the flag on every OS. An
    # absent flag passes here and reaches the result as an unverified fact.
    if any(partition.is_active for partition in inspection.partitions):
        refusals.append(
            Refusal(RefusalCode.ACTIVE_PARTITION, "The drive holds an active boot partition.")
        )
    return refusals


def _collect_media_refusals(inspection: DriveInspection) -> list[Refusal]:
    """Return refusals for read-only media and filesystem errors."""
    partitions = inspection.partitions
    refusals: list[Refusal] = []
    if inspection.drive.is_read_only or any(part.is_read_only for part in partitions):
        refusals.append(
            Refusal(RefusalCode.READ_ONLY_MEDIA, "The drive is read-only.", READ_ONLY_GUIDANCE)
        )
    if any(part.filesystem_health is FilesystemHealth.ERRORS for part in partitions):
        refusals.append(
            Refusal(
                RefusalCode.FILESYSTEM_ERRORS,
                "The filesystem check found errors.",
                REPAIR_GUIDANCE,
            )
        )
    return refusals


def _collect_layout_refusals(partition: PartitionInfo) -> list[Refusal]:
    """Return refusals for the scheme, partition kind, filesystem, and mount state."""
    refusals: list[Refusal] = []
    scheme = partition.partition_scheme
    if scheme is None:
        refusals.append(
            Refusal(RefusalCode.UNKNOWN_PARTITION_LAYOUT, "The partition layout is unknown.")
        )
    elif scheme.casefold() not in MBR_SCHEME_NAMES:
        refusals.append(
            Refusal(
                RefusalCode.UNSUPPORTED_PARTITION_SCHEME,
                "The drive does not use an MBR partition table.",
            )
        )
    if partition.is_primary is False:
        refusals.append(
            Refusal(RefusalCode.NOT_PRIMARY_PARTITION, "The partition is not a primary partition.")
        )
    if partition.filesystem is None:
        refusals.append(Refusal(RefusalCode.UNKNOWN_FILESYSTEM, "The filesystem is unknown."))
    elif not partition.is_fat32:
        refusals.append(
            Refusal(
                RefusalCode.UNSUPPORTED_FILESYSTEM,
                "The filesystem is not FAT32.",
                "Choose a FAT32 drive. InfinitePrep never formats a drive.",
            )
        )
    if not partition.mount_points:
        refusals.append(
            Refusal(
                RefusalCode.NOT_MOUNTED,
                "The partition is not mounted.",
                "Mount the drive with the operating system, then retry.",
            )
        )
    return refusals


def _collect_request_refusals(request: PreflightRequest) -> list[Refusal]:
    """Return refusals for unknown database formats and files FAT32 cannot hold."""
    refusals: list[Refusal] = []
    unsupported = [
        profile.name for profile in request.profiles if not profile.database_format_supported
    ]
    if unsupported:
        names = ", ".join(unsupported)
        refusals.append(
            Refusal(
                RefusalCode.UNKNOWN_DATABASE_FORMAT,
                f"Database format support is unknown for: {names}.",
            )
        )
    blocked_count = sum(1 for size in request.file_sizes if size >= FAT32_BLOCKED_FILE_BYTES)
    if blocked_count:
        refusals.append(
            Refusal(
                RefusalCode.FILE_TOO_LARGE,
                f"{blocked_count} selected file(s) reach the FAT32 limit of 4 GiB.",
                "Remove or convert each oversized file, then retry.",
            )
        )
    return refusals


def _collect_space_refusals(available_bytes: int | None, required_bytes: int) -> list[Refusal]:
    """Return a refusal when free space is unknown or below the requirement."""
    if available_bytes is None:
        return [Refusal(RefusalCode.SPACE_UNKNOWN, "The free space of the drive is unknown.")]
    if available_bytes < required_bytes:
        return [
            Refusal(
                RefusalCode.INSUFFICIENT_SPACE,
                f"The export needs {required_bytes} bytes. The drive has {available_bytes}.",
                "Free space on the drive or reduce the export, then retry.",
            )
        ]
    return []


def _list_unverified_facts(
    inspection: DriveInspection, partition: PartitionInfo | None
) -> tuple[str, ...]:
    """Return sorted names of facts that are absent or came from a fallback source."""
    names = set(inspection.inferred_facts)
    if partition is not None:
        optional_facts = (
            (FACT_ACTIVE_PARTITION, partition.is_active),
            (FACT_FILESYSTEM_HEALTH, partition.filesystem_health),
            (FACT_PRIMARY_PARTITION, partition.is_primary),
        )
        names.update(name for name, value in optional_facts if value is None)
    return tuple(sorted(names))


def _collect_target_refusals(
    inspection: DriveInspection, partition: PartitionInfo | None
) -> list[Refusal]:
    """Return media, partition count, and layout refusals in rule order."""
    refusals = _collect_media_refusals(inspection)
    if partition is None:
        refusals.append(
            Refusal(
                RefusalCode.PARTITION_COUNT,
                "The drive does not hold exactly one partition.",
            )
        )
        return refusals
    refusals.extend(_collect_layout_refusals(partition))
    return refusals


def evaluate_preflight(
    inspection: DriveInspection,
    request: PreflightRequest,
    reserve: ReservePolicy | None = None,
) -> PreflightResult:
    """Evaluate every preflight rule for one target and one export request.

    Safety refusals return alone. They hide the media, layout, request, and
    space rules, because a drive that must never receive an export shows no
    further detail. Without a safety refusal the result lists every refusal.

    An unmounted partition yields NOT_MOUNTED only. The space rule needs a
    mount path and does not run without one.

    An absent active, primary, or health fact does not refuse. The fact name
    appears in unverified_facts for display. An absent system state refuses.

    Raises ValueError for a negative size, count, or reserve value.
    """
    estimate = estimate_export_bytes(
        request.file_sizes, request.directory_count, request.database_allowance_bytes
    )
    reserve_bytes = compute_reserve_bytes(estimate, reserve or ReservePolicy())
    partition = _select_single_partition(inspection)
    mount_path = partition.mount_points[0] if partition and partition.mount_points else None
    space = inspection.space_for_mount(mount_path) if mount_path else None
    available = space.available_bytes if space else None
    refusals = _collect_safety_refusals(inspection)
    if not refusals:
        refusals = _collect_target_refusals(inspection, partition)
        refusals.extend(_collect_request_refusals(request))
        if mount_path is not None:
            refusals.extend(_collect_space_refusals(available, estimate + reserve_bytes))
    return PreflightResult(
        refusals=tuple(refusals),
        unverified_facts=_list_unverified_facts(inspection, partition),
        estimate_bytes=estimate,
        reserve_bytes=reserve_bytes,
        available_bytes=available,
        mount_path=mount_path,
    )
