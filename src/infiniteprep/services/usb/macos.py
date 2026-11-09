"""Read-only macOS drive and partition inspection using diskutil (D161, D183, D203, Rule 1).

This module executes fixed argument arrays without shell interpolation to inspect
connected storage devices, partition schemes, filesystems, and available space.
It parses only plist output and performs strictly read-only queries (list, info).
It treats all USB-supplied metadata as untrusted input.
"""

from __future__ import annotations

import plistlib
import re
import subprocess
from collections.abc import Callable
from typing import Any, Final
from xml.parsers.expat import ExpatError

from infiniteprep.services.usb._shared import (
    clean_int,
    clean_str,
    default_command_runner,
    sanitize_mount_point,
)
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveIdentity,
    DriveInspection,
    InspectionParseError,
    PartitionInfo,
    SpaceInfo,
)

DISKUTIL_LIST_COMMAND: Final[tuple[str, ...]] = ("diskutil", "list", "-plist")
DISKUTIL_INFO_COMMAND: Final[tuple[str, ...]] = ("diskutil", "info", "-plist")
MACOS_DEVICE_SAFE_RE: Final[re.Pattern[str]] = re.compile(r"^disk\d+(?:s\d+)*$")


def _sanitize_diskutil_identifier(val: Any) -> str:
    """Validate and sanitize a diskutil device identifier from untrusted output."""
    raw = clean_str(val)
    if not raw or ".." in raw or "/" in raw or not MACOS_DEVICE_SAFE_RE.match(raw):
        raise InspectionParseError(f"Invalid or unsafe diskutil device identifier: {val!r}")
    return raw


def _partition_number_from_identifier(identifier: str, fallback_index: int) -> int:
    """Derive a partition number from a diskNsM identifier suffix, or the fallback index."""
    groups = re.findall(r"(?:s(\d+))+$", identifier)
    if groups and groups[-1].isdigit() and int(groups[-1]) > 0:
        return int(groups[-1])
    return fallback_index


def _normalize_filesystem(val: Any) -> str | None:
    """Normalize macOS filesystem names so FAT32 and exFAT variants are detectable."""
    raw = clean_str(val)
    if not raw:
        return None
    lowered = raw.casefold()
    if "fat32" in lowered:
        return "FAT32"
    if "exfat" in lowered:
        return "exFAT"
    return raw


def _info_flag(info: dict[str, Any], key: str) -> bool:
    """Return a boolean plist flag, treating missing keys as False."""
    return bool(info.get(key))


class MacOSDriveInspector:
    """Inspects macOS drives, partitions, filesystems, and free space in read-only mode."""

    def __init__(
        self,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """Initialize with optional command runner for hermetic testing."""
        self._command_runner = command_runner or default_command_runner

    def inspect_all(self) -> tuple[DriveInspection, ...]:
        """Inspect all disks reported by diskutil list and return DriveInspection records."""
        entries = self._run_and_parse_list()
        return tuple(self._build_drive_inspection(entry) for entry in entries)

    def inspect_device(self, device_name_or_path: str) -> DriveInspection:
        """Inspect a device by identifier (e.g. 'disk2') or path (e.g. '/dev/disk2s1')."""
        target = clean_str(device_name_or_path)
        if not target or ".." in target:
            raise CommandExecutionError(f"Device '{device_name_or_path}' is invalid or unsafe.")
        target_name = target.removeprefix("/dev/")
        if "/" in target_name or not MACOS_DEVICE_SAFE_RE.match(target_name):
            raise CommandExecutionError(f"Device '{device_name_or_path}' is invalid or unsafe.")
        for inspection in self.inspect_all():
            drive = inspection.drive
            if drive.name == target_name or drive.path in (target, f"/dev/{target_name}"):
                return inspection
            if any(
                p.name == target_name or p.path in (target, f"/dev/{target_name}")
                for p in inspection.partitions
            ):
                return inspection

        raise CommandExecutionError(f"Device '{device_name_or_path}' was not found.")

    def _run_and_parse_list(self) -> list[dict[str, Any]]:
        """Run diskutil list -plist and validate the disk entry list."""
        try:
            completed = self._command_runner(list(DISKUTIL_LIST_COMMAND))
        except (subprocess.SubprocessError, OSError) as exc:
            raise CommandExecutionError(f"diskutil list execution failed: {exc}") from exc

        if completed.returncode != 0:
            err = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise CommandExecutionError(f"diskutil list failed with {err}")

        data = self._parse_plist_dict(completed.stdout, "diskutil list")
        if "AllDisksAndPartitions" not in data:
            raise InspectionParseError("diskutil list output missing 'AllDisksAndPartitions' list.")

        entries = data["AllDisksAndPartitions"]
        if not isinstance(entries, list):
            raise InspectionParseError("diskutil list 'AllDisksAndPartitions' is not a list.")

        for entry in entries:
            if not isinstance(entry, dict):
                raise InspectionParseError("diskutil list disk entry is not a mapping.")

        self._validate_entries(entries)
        return entries

    @staticmethod
    def _validate_entries(entries: list[dict[str, Any]]) -> None:
        """Validate identifiers and partition structure before any info invocation."""
        for entry in entries:
            _sanitize_diskutil_identifier(entry.get("DeviceIdentifier"))
            for part_dict in MacOSDriveInspector._partition_entries(entry):
                _sanitize_diskutil_identifier(part_dict.get("DeviceIdentifier"))

    def _run_info_plist(self, identifier: str) -> dict[str, Any]:
        """Run diskutil info -plist for one device identifier and validate the mapping."""
        command = [*DISKUTIL_INFO_COMMAND, identifier]
        try:
            completed = self._command_runner(list(command))
        except (subprocess.SubprocessError, OSError) as exc:
            raise CommandExecutionError(f"diskutil info execution failed: {exc}") from exc

        if completed.returncode != 0:
            err = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise CommandExecutionError(f"diskutil info failed with {err}")

        return self._parse_plist_dict(completed.stdout, "diskutil info")

    @staticmethod
    def _parse_plist_dict(stdout: str, source: str) -> dict[str, Any]:
        """Parse plist bytes into a mapping, mapping all parse failures to InspectionParseError."""
        try:
            data = plistlib.loads(stdout.encode("utf-8"))
        except (ValueError, ExpatError) as exc:
            # plistlib.InvalidFileException subclasses ValueError; XML faults raise ExpatError.
            raise InspectionParseError(f"{source} returned malformed plist: {exc}") from exc
        if not isinstance(data, dict):
            raise InspectionParseError(f"{source} output is not a mapping.")
        return data

    def _build_drive_inspection(self, entry: dict[str, Any]) -> DriveInspection:
        """Construct a DriveInspection record from one AllDisksAndPartitions entry."""
        identifier = _sanitize_diskutil_identifier(entry.get("DeviceIdentifier"))
        disk_info = self._run_info_plist(identifier)
        identity = self._build_drive_identity(entry, disk_info, identifier)
        scheme = clean_str(entry.get("Content"))
        partitions: list[PartitionInfo] = []
        space_entries: list[tuple[str, SpaceInfo]] = []
        fallback_index = 1
        for part_dict in self._partition_entries(entry):
            partition, spaces = self._build_partition(part_dict, scheme, fallback_index)
            fallback_index += 1
            partitions.append(partition)
            space_entries.extend(spaces)
        return DriveInspection(
            drive=identity,
            partitions=tuple(partitions),
            space_by_mount=self._deduplicate_space_entries(space_entries),
        )

    def _build_drive_identity(
        self,
        entry: dict[str, Any],
        disk_info: dict[str, Any],
        identifier: str,
    ) -> DriveIdentity:
        """Construct sanitized DriveIdentity from list and info plist dictionaries."""
        return DriveIdentity(
            name=identifier,
            path=f"/dev/{identifier}",
            size_bytes=clean_int(entry.get("Size"))
            or clean_int(disk_info.get("DiskSize"))
            or clean_int(disk_info.get("TotalSize")),
            model=clean_str(disk_info.get("DeviceName")) or clean_str(disk_info.get("MediaName")),
            is_removable=_info_flag(disk_info, "RemovableMedia")
            or _info_flag(disk_info, "Ejectable"),
            is_read_only=_info_flag(disk_info, "Read-Only Media"),
            bus_type=clean_str(disk_info.get("BusProtocol"))
            or clean_str(disk_info.get("Protocol")),
            subsystem=clean_str(disk_info.get("DeviceTreePath")),
        )

    @staticmethod
    def _partition_entries(entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect partition and APFS volume mappings, validating plist structure."""
        collected: list[dict[str, Any]] = []
        for key in ("Partitions", "APFSVolumes"):
            raw = entry.get(key)
            if raw is None:
                continue
            if not isinstance(raw, list):
                raise InspectionParseError(f"diskutil '{key}' must be a list or null.")
            for item in raw:
                if not isinstance(item, dict):
                    raise InspectionParseError(f"diskutil '{key}' entry is not a mapping.")
                collected.append(item)
        return collected

    def _build_partition(
        self,
        part_dict: dict[str, Any],
        parent_scheme: str | None,
        fallback_index: int,
    ) -> tuple[PartitionInfo, list[tuple[str, SpaceInfo]]]:
        """Parse one partition entry, querying volume info for mounted volumes only."""
        identifier = _sanitize_diskutil_identifier(part_dict.get("DeviceIdentifier"))
        mount_point = sanitize_mount_point(part_dict.get("MountPoint"))
        mount_points = (mount_point,) if mount_point else ()
        volume_info = self._run_info_plist(identifier) if mount_point else None
        space_entries: list[tuple[str, SpaceInfo]] = []
        if volume_info is not None:
            space = self._space_from_volume_info(volume_info)
            if space is not None:
                space_entries.append((mount_point, space))
        partition = PartitionInfo(
            name=identifier,
            path=f"/dev/{identifier}",
            size_bytes=clean_int(part_dict.get("Size")),
            partition_number=_partition_number_from_identifier(identifier, fallback_index),
            partition_scheme=parent_scheme,
            partition_type=clean_str(part_dict.get("PartitionType"))
            or clean_str(part_dict.get("Content")),
            filesystem=_normalize_filesystem(part_dict.get("FilesystemType")),
            label=clean_str(part_dict.get("VolumeName")),
            uuid=clean_str(part_dict.get("VolumeUUID"))
            or clean_str(part_dict.get("PartitionUUID")),
            mount_points=mount_points,
            is_read_only=_info_flag(volume_info, "Read-Only Volume") if volume_info else False,
        )
        return partition, space_entries

    @staticmethod
    def _space_from_volume_info(info: dict[str, Any]) -> SpaceInfo | None:
        """Build SpaceInfo from volume info, skipping entries without size fields."""
        if "TotalSize" not in info or "VolumeFreeSpace" not in info:
            return None
        total_bytes = clean_int(info.get("TotalSize"))
        available_bytes = clean_int(info.get("VolumeFreeSpace"))
        used_bytes = max(0, total_bytes - available_bytes)
        return SpaceInfo(
            total_bytes=total_bytes,
            used_bytes=used_bytes,
            available_bytes=available_bytes,
        )

    @staticmethod
    def _deduplicate_space_entries(
        space_entries: list[tuple[str, SpaceInfo]],
    ) -> tuple[tuple[str, SpaceInfo], ...]:
        """Deduplicate space entries by mount point while preserving order."""
        seen_mounts: set[str] = set()
        unique: list[tuple[str, SpaceInfo]] = []
        for mount, space in space_entries:
            if mount in seen_mounts:
                continue
            seen_mounts.add(mount)
            unique.append((mount, space))
        return tuple(unique)
