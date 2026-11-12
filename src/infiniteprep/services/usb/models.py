"""Domain models for USB and removable drive inspection (D93, D160, D161, D183)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FilesystemHealth(StrEnum):
    """Result of a read-only filesystem check. An absent value means not checked."""

    HEALTHY = "healthy"
    ERRORS = "errors"


class DriveInspectionError(Exception):
    """Base exception for all drive inspection failures."""


class CommandExecutionError(DriveInspectionError):
    """Raised when an external inspection command fails or times out."""


class InspectionParseError(DriveInspectionError):
    """Raised when output from an inspection command is malformed or invalid."""


@dataclass(frozen=True)
class SpaceInfo:
    """Byte-accurate filesystem capacity and utilization statistics."""

    total_bytes: int
    used_bytes: int
    available_bytes: int


@dataclass(frozen=True)
class PartitionInfo:
    """Inspection record for a single storage volume or partition."""

    name: str
    path: str
    size_bytes: int
    partition_number: int | None = None
    partition_scheme: str | None = None
    partition_type: str | None = None
    filesystem: str | None = None
    label: str | None = None
    uuid: str | None = None
    mount_points: tuple[str, ...] = field(default_factory=tuple)
    is_read_only: bool = False
    # Preflight facts. None means no probe supplied the fact.
    is_active: bool | None = None
    is_primary: bool | None = None
    filesystem_health: FilesystemHealth | None = None

    @property
    def is_exfat(self) -> bool:
        """Return True if this partition has an exFAT filesystem."""
        return (self.filesystem or "").casefold() == "exfat"

    @property
    def is_fat32(self) -> bool:
        """Return True if this partition has a FAT32/vfat filesystem."""
        return (self.filesystem or "").casefold() in ("vfat", "fat32")


@dataclass(frozen=True)
class DriveIdentity:
    """Hardware identity and storage bus attributes of a physical or virtual disk."""

    name: str
    path: str
    size_bytes: int
    model: str | None = None
    vendor: str | None = None
    serial: str | None = None
    is_removable: bool = False
    is_read_only: bool = False
    bus_type: str | None = None
    subsystem: str | None = None
    # Preflight facts. None means no probe supplied the fact.
    hardware_id: str | None = None
    is_system: bool | None = None


@dataclass(frozen=True)
class DriveInspection:
    """Comprehensive read-only inspection report for a drive and its partitions."""

    drive: DriveIdentity
    partitions: tuple[PartitionInfo, ...] = field(default_factory=tuple)
    space_by_mount: tuple[tuple[str, SpaceInfo], ...] = field(default_factory=tuple)
    # Names of preflight facts that came from a passive fallback source.
    inferred_facts: frozenset[str] = field(default_factory=frozenset)

    @property
    def space_map(self) -> dict[str, SpaceInfo]:
        """Return a mapping of mount point to SpaceInfo."""
        return dict(self.space_by_mount)

    def space_for_mount(self, mount_point: str) -> SpaceInfo | None:
        """Return space statistics for a given mount point if recorded."""
        return self.space_map.get(mount_point)

    @property
    def has_exfat_partition(self) -> bool:
        """Return True if any partition on this drive has an exFAT filesystem."""
        return any(p.is_exfat for p in self.partitions)

    @property
    def has_fat32_partition(self) -> bool:
        """Return True if any partition on this drive has a FAT32 filesystem."""
        return any(p.is_fat32 for p in self.partitions)
