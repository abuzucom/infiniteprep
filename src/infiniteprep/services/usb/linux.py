"""Read-only Linux drive and partition inspection using lsblk and df (D161, D183, Rule 1).

This module executes fixed argument arrays without shell interpolation to inspect
connected storage devices, partition schemes, filesystems, and available space.
It performs strictly read-only queries and never alters device state.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveIdentity,
    DriveInspection,
    InspectionParseError,
    PartitionInfo,
    SpaceInfo,
)

LSBLK_COLUMNS: Final[str] = (
    "NAME,PATH,MODEL,VENDOR,SERIAL,RM,RO,TYPE,FSTYPE,LABEL,UUID,FSAVAIL,FSUSED,FSSIZE,MOUNTPOINTS,PTTYPE,PARTTYPE,SIZE"
)
LSBLK_COMMAND: Final[tuple[str, ...]] = (
    "lsblk",
    "-J",
    "-b",
    "-o",
    LSBLK_COLUMNS,
)
DF_COMMAND: Final[tuple[str, ...]] = ("df", "-P", "-B1")
DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[float] = 15.0


def _clean_str(val: Any) -> str | None:
    """Return stripped string or None if empty or null."""
    if val is None:
        return None
    text = str(val).strip()
    return text if text else None


def _clean_int(val: Any, default: int = 0) -> int:
    """Convert value to non-negative int, returning default on failure."""
    if val is None:
        return default
    try:
        parsed = int(val)
        return max(0, parsed)
    except (ValueError, TypeError):
        return default


class LinuxDriveInspector:
    """Inspects Linux drives, partitions, filesystems, and free space in read-only mode."""

    def __init__(
        self,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """Initialize with optional command runner for hermetic testing."""
        self._command_runner = command_runner or self._default_runner

    @staticmethod
    def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Execute a fixed command array safely using subprocess.run with shell=False."""
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )

    def inspect_all(self) -> tuple[DriveInspection, ...]:
        """Inspect all block devices and return DriveInspection records."""
        lsblk_devices = self._run_and_parse_lsblk()
        df_space = self._run_and_parse_df()

        inspections: list[DriveInspection] = []
        for dev in lsblk_devices:
            # We focus on disk devices or top-level block storage
            dev_type = str(dev.get("type") or "").casefold()
            if dev_type not in ("disk", "loop", "rom", "dm"):
                continue
            inspection = self._build_drive_inspection(dev, df_space)
            inspections.append(inspection)

        return tuple(inspections)

    def inspect_device(self, device_name_or_path: str) -> DriveInspection:
        """Inspect a specific device by name (e.g. 'sdb') or device path (e.g. '/dev/sdb')."""
        target = device_name_or_path.strip()
        target_name = target.removeprefix("/dev/")
        all_drives = self.inspect_all()

        for inspection in all_drives:
            drive = inspection.drive
            if drive.name == target_name or drive.path in (target, f"/dev/{target_name}"):
                return inspection

        raise CommandExecutionError(f"Device '{device_name_or_path}' was not found.")

    def _run_and_parse_lsblk(self) -> list[dict[str, Any]]:
        """Run lsblk and parse JSON output."""
        try:
            completed = self._command_runner(list(LSBLK_COMMAND))
        except (subprocess.SubprocessError, OSError) as exc:
            raise CommandExecutionError(f"lsblk execution failed: {exc}") from exc

        if completed.returncode != 0:
            err = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise CommandExecutionError(f"lsblk failed with {err}")

        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise InspectionParseError(f"lsblk returned malformed JSON: {exc}") from exc

        if not isinstance(data, dict) or "blockdevices" not in data:
            raise InspectionParseError("lsblk output missing 'blockdevices' list.")

        devices = data["blockdevices"]
        if not isinstance(devices, list):
            raise InspectionParseError("lsblk 'blockdevices' is not a list.")

        return devices

    def _run_and_parse_df(self) -> dict[str, SpaceInfo]:
        """Run df -P -B1 and parse mount points to SpaceInfo."""
        try:
            completed = self._command_runner(list(DF_COMMAND))
        except (subprocess.SubprocessError, OSError) as exc:
            raise CommandExecutionError(f"df execution failed: {exc}") from exc

        if completed.returncode != 0:
            err = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise CommandExecutionError(f"df failed with {err}")

        return self._parse_df_output(completed.stdout)

    @staticmethod
    def _parse_df_output(stdout: str) -> dict[str, SpaceInfo]:
        """Parse POSIX format df -P output into a mountpoint-to-SpaceInfo map."""
        space_map: dict[str, SpaceInfo] = {}
        lines = stdout.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Header line: Filesystem 1024-blocks Used Available Capacity Mounted on
            if index == 0 and "Mounted on" in stripped:
                continue

            parts = stripped.split()
            if len(parts) < 6:
                continue

            # In POSIX df format:
            # parts[0]: filesystem
            # parts[1]: 1-blocks (total)
            # parts[2]: used
            # parts[3]: available
            # parts[4]: capacity %
            # parts[5:]: mount point (may contain spaces)
            try:
                total_bytes = _clean_int(parts[1])
                used_bytes = _clean_int(parts[2])
                available_bytes = _clean_int(parts[3])
                mount_point = " ".join(parts[5:])
                space_map[mount_point] = SpaceInfo(
                    total_bytes=total_bytes,
                    used_bytes=used_bytes,
                    available_bytes=available_bytes,
                )
            except (ValueError, IndexError):
                continue

        return space_map

    def _build_drive_inspection(
        self,
        dev: dict[str, Any],
        df_space: dict[str, SpaceInfo],
    ) -> DriveInspection:
        """Construct a DriveInspection record from a blockdevice dictionary."""
        identity = self._build_drive_identity(dev)
        partition_scheme = _clean_str(dev.get("pttype"))
        partitions, space_entries = self._collect_partitions_and_spaces(
            dev,
            partition_scheme=partition_scheme,
            df_space=df_space,
        )
        unique_space_entries = self._deduplicate_spaces(space_entries)

        return DriveInspection(
            drive=identity,
            partitions=tuple(partitions),
            space_by_mount=unique_space_entries,
        )

    @staticmethod
    def _build_drive_identity(dev: dict[str, Any]) -> DriveIdentity:
        """Extract DriveIdentity from a blockdevice dictionary."""
        name = str(dev.get("name") or "")
        path = str(dev.get("path") or (f"/dev/{name}" if name else ""))
        size_bytes = _clean_int(dev.get("size"))

        return DriveIdentity(
            name=name,
            path=path,
            size_bytes=size_bytes,
            model=_clean_str(dev.get("model")),
            vendor=_clean_str(dev.get("vendor")),
            serial=_clean_str(dev.get("serial")),
            is_removable=bool(dev.get("rm")),
            is_read_only=bool(dev.get("ro")),
            bus_type=_clean_str(dev.get("tran")),
            subsystem=_clean_str(dev.get("subsystems")),
        )

    def _collect_partitions_and_spaces(
        self,
        dev: dict[str, Any],
        partition_scheme: str | None,
        df_space: dict[str, SpaceInfo],
    ) -> tuple[list[PartitionInfo], list[tuple[str, SpaceInfo]]]:
        """Collect partition records and space entries from device or its children."""
        partitions: list[PartitionInfo] = []
        space_entries: list[tuple[str, SpaceInfo]] = []

        children = dev.get("children")
        if isinstance(children, list) and children:
            for child_index, child in enumerate(children, start=1):
                part, part_spaces = self._parse_partition(
                    child,
                    parent_scheme=partition_scheme,
                    fallback_index=child_index,
                    df_space=df_space,
                )
                partitions.append(part)
                space_entries.extend(part_spaces)
        else:
            fstype = _clean_str(dev.get("fstype"))
            mount_points = self._extract_mountpoints(dev)
            if fstype or mount_points:
                part, part_spaces = self._parse_partition(
                    dev,
                    parent_scheme=partition_scheme,
                    fallback_index=1,
                    df_space=df_space,
                )
                partitions.append(part)
                space_entries.extend(part_spaces)

        return partitions, space_entries

    @staticmethod
    def _deduplicate_spaces(
        space_entries: list[tuple[str, SpaceInfo]],
    ) -> tuple[tuple[str, SpaceInfo], ...]:
        """Deduplicate space entries by mount point while preserving order."""
        seen_mounts: set[str] = set()
        unique_space_entries: list[tuple[str, SpaceInfo]] = []
        for mount, space in space_entries:
            if mount not in seen_mounts:
                seen_mounts.add(mount)
                unique_space_entries.append((mount, space))
        return tuple(unique_space_entries)

    def _parse_partition(
        self,
        part_dict: dict[str, Any],
        parent_scheme: str | None,
        fallback_index: int,
        df_space: dict[str, SpaceInfo],
    ) -> tuple[PartitionInfo, list[tuple[str, SpaceInfo]]]:
        """Parse one partition entry and collect its associated space metrics."""
        mount_points = self._extract_mountpoints(part_dict)
        partition_info = self._build_partition_info(
            part_dict,
            parent_scheme=parent_scheme,
            fallback_index=fallback_index,
            mount_points=mount_points,
        )
        spaces = self._collect_partition_spaces(part_dict, mount_points, df_space)
        return partition_info, spaces

    @staticmethod
    def _build_partition_info(
        part_dict: dict[str, Any],
        parent_scheme: str | None,
        fallback_index: int,
        mount_points: tuple[str, ...],
    ) -> PartitionInfo:
        """Construct PartitionInfo from raw dictionary attributes."""
        name = str(part_dict.get("name") or "")
        path = str(part_dict.get("path") or (f"/dev/{name}" if name else ""))

        return PartitionInfo(
            name=name,
            path=path,
            size_bytes=_clean_int(part_dict.get("size")),
            partition_number=fallback_index,
            partition_scheme=_clean_str(part_dict.get("pttype")) or parent_scheme,
            partition_type=_clean_str(part_dict.get("parttype")),
            filesystem=_clean_str(part_dict.get("fstype")),
            label=_clean_str(part_dict.get("label")),
            uuid=_clean_str(part_dict.get("uuid")),
            mount_points=mount_points,
            is_read_only=bool(part_dict.get("ro")),
        )

    def _collect_partition_spaces(
        self,
        part_dict: dict[str, Any],
        mount_points: tuple[str, ...],
        df_space: dict[str, SpaceInfo],
    ) -> list[tuple[str, SpaceInfo]]:
        """Resolve SpaceInfo for all mount points of a partition."""
        spaces: list[tuple[str, SpaceInfo]] = []
        for mount in mount_points:
            if mount in df_space:
                spaces.append((mount, df_space[mount]))
                continue
            space = self._extract_lsblk_space(part_dict)
            if space is not None:
                spaces.append((mount, space))
        return spaces

    @staticmethod
    def _extract_lsblk_space(part_dict: dict[str, Any]) -> SpaceInfo | None:
        """Extract fallback SpaceInfo directly from lsblk fields."""
        fssize = part_dict.get("fssize")
        fsavail = part_dict.get("fsavail")
        if fssize is None or fsavail is None:
            return None
        total_b = _clean_int(fssize)
        avail_b = _clean_int(fsavail)
        fsused = part_dict.get("fsused")
        used_b = _clean_int(fsused) if fsused is not None else max(0, total_b - avail_b)
        return SpaceInfo(total_bytes=total_b, used_bytes=used_b, available_bytes=avail_b)

    @staticmethod
    def _extract_mountpoints(device_entry: dict[str, Any]) -> tuple[str, ...]:
        """Extract and clean all non-null mount points from a device or partition entry."""
        mounts: list[str] = []
        raw_mounts = device_entry.get("mountpoints")
        if isinstance(raw_mounts, list):
            for m in raw_mounts:
                if m is not None and str(m).strip():
                    mounts.append(str(m).strip())
        elif device_entry.get("mountpoint"):
            m = str(device_entry["mountpoint"]).strip()
            if m:
                mounts.append(m)
        return tuple(dict.fromkeys(mounts))  # preserve order, eliminate duplicates
