"""Domain models for USB and removable drive inspection (D93, D160, D161, D183)."""

from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class DriveInspection:
    """Comprehensive read-only inspection report for a drive and its partitions."""

    drive: DriveIdentity
    partitions: tuple[PartitionInfo, ...] = field(default_factory=tuple)
    space_by_mount: tuple[tuple[str, SpaceInfo], ...] = field(default_factory=tuple)

    @property
    def space_map(self) -> dict[str, SpaceInfo]:
        """Return a mapping of mount point to SpaceInfo."""
        return dict(self.space_by_mount)

    def space_for_mount(self, mount_point: str) -> SpaceInfo | None:
        """Return space statistics for a given mount point if recorded."""
        return self.space_map.get(mount_point)
