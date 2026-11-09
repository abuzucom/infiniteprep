"""Read-only Linux drive and partition inspection using lsblk and df (D161, D183, Rule 1).

This module executes fixed argument arrays without shell interpolation to inspect
connected storage devices, partition schemes, filesystems, and available space.
It performs strictly read-only queries and treats all USB-supplied metadata as
untrusted input.
"""

from __future__ import annotations

import json
import re
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
    "NAME,PATH,MODEL,VENDOR,SERIAL,RM,RO,TYPE,FSTYPE,LABEL,UUID,FSAVAIL,FSUSED,FSSIZE,MOUNTPOINTS,PTTYPE,PARTTYPE,SIZE,TRAN,SUBSYSTEMS,PARTN"
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
MAX_METADATA_STR_LEN: Final[int] = 512
ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
DEVICE_NAME_SAFE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_\-\./]+$")


def _clean_str(val: Any, max_len: int = MAX_METADATA_STR_LEN) -> str | None:
    """Sanitize untrusted metadata string, removing escape sequences and control characters."""
    if val is None:
        return None
    raw = str(val)
    no_ansi = ANSI_ESCAPE_RE.sub("", raw)
    filtered = "".join(ch for ch in no_ansi if ch.isprintable() and ch != "\x00")
    stripped = filtered.strip()
    if not stripped:
        return None
    return stripped[:max_len]


def _clean_int(val: Any, default: int = 0) -> int:
    """Convert value to non-negative int, returning default on failure."""
    if val is None:
        return default
    try:
        parsed = int(val)
        return max(0, parsed)
    except (ValueError, TypeError):
        return default


def _sanitize_device_name(val: Any) -> str:
    """Validate and sanitize device name from untrusted output."""
    raw = _clean_str(val)
    if not raw or ".." in raw or "/" in raw or not DEVICE_NAME_SAFE_RE.match(raw):
        raise InspectionParseError(f"Invalid or unsafe block device name: {val!r}")
    return raw


def _sanitize_device_path(raw_path: Any, safe_name: str) -> str:
    """Validate device path or synthesize safe /dev/{name}."""
    cleaned = _clean_str(raw_path)
    if cleaned:
        if ".." in cleaned or not cleaned.startswith("/dev/"):
            raise InspectionParseError(f"Unsafe block device path: {raw_path!r}")
        suffix = cleaned.removeprefix("/dev/")
        if not suffix or not DEVICE_NAME_SAFE_RE.match(suffix):
            raise InspectionParseError(f"Invalid characters in device path: {raw_path!r}")
        return cleaned
    return f"/dev/{safe_name}"


def _sanitize_mount_point(val: Any) -> str | None:
    """Sanitize untrusted mountpoint path, preventing traversal and control characters."""
    cleaned = _clean_str(val)
    if not cleaned or ".." in cleaned or not cleaned.startswith("/"):
        return None
    return cleaned


def _extract_partition_number(part_dict: dict[str, Any], fallback_index: int) -> int:
    """Determine partition number from PARTN column, name suffix, or fallback index."""
    partn_val = _clean_str(part_dict.get("partn"))
    if partn_val and partn_val.isdigit():
        partn_int = int(partn_val)
        if partn_int > 0:
            return partn_int

    name_str = _clean_str(part_dict.get("name")) or ""
    match = re.search(r"(\d+)$", name_str)
    if match and match.group(1).isdigit():
        num = int(match.group(1))
        if num > 0:
            return num

    return fallback_index


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
            dev_type = str(dev.get("type") or "").casefold()
            if dev_type in ("disk", "loop", "rom", "dm"):
                inspections.append(self._build_drive_inspection(dev, df_space))
        return tuple(inspections)

    def inspect_device(self, device_name_or_path: str) -> DriveInspection:
        """Inspect a specific device by name (e.g. 'sdb') or device path (e.g. '/dev/sdb')."""
        target = _clean_str(device_name_or_path)
        if not target or ".." in target:
            raise CommandExecutionError(f"Device '{device_name_or_path}' is invalid or unsafe.")
        target_name = target.removeprefix("/dev/")
        for inspection in self.inspect_all():
            drive = inspection.drive
            if drive.name == target_name or drive.path in (target, f"/dev/{target_name}"):
                return inspection

        raise CommandExecutionError(f"Device '{device_name_or_path}' was not found.")

    def _run_and_parse_lsblk(self) -> list[dict[str, Any]]:
        """Run lsblk and parse JSON output, ensuring all entries are mappings."""
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

        for dev in devices:
            if not isinstance(dev, dict):
                raise InspectionParseError("lsblk device entry is not a mapping.")

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
        for index, line in enumerate(stdout.splitlines()):
            stripped = line.strip()
            if not stripped or (index == 0 and "Mounted on" in stripped):
                continue

            parts = stripped.split()
            if len(parts) < 6:
                continue

            if not (parts[1].isdigit() and parts[2].isdigit() and parts[3].isdigit()):
                continue

            mount_point = _sanitize_mount_point(" ".join(parts[5:]))
            if not mount_point:
                continue

            space_map[mount_point] = SpaceInfo(
                total_bytes=int(parts[1]),
                used_bytes=int(parts[2]),
                available_bytes=int(parts[3]),
            )

        return space_map

    @staticmethod
    def _build_drive_identity(dev: dict[str, Any]) -> DriveIdentity:
        """Construct sanitized DriveIdentity from a blockdevice dictionary."""
        name = _sanitize_device_name(dev.get("name"))
        path = _sanitize_device_path(dev.get("path"), name)
        return DriveIdentity(
            name=name,
            path=path,
            size_bytes=_clean_int(dev.get("size")),
            model=_clean_str(dev.get("model")),
            vendor=_clean_str(dev.get("vendor")),
            serial=_clean_str(dev.get("serial")),
            is_removable=bool(dev.get("rm")),
            is_read_only=bool(dev.get("ro")),
            bus_type=_clean_str(dev.get("tran")),
            subsystem=_clean_str(dev.get("subsystems")),
        )

    def _collect_partitions(
        self,
        dev: dict[str, Any],
        parent_scheme: str | None,
        df_space: dict[str, SpaceInfo],
    ) -> tuple[list[PartitionInfo], list[tuple[str, SpaceInfo]]]:
        """Parse partitions from children or whole disk, validating types."""
        partitions: list[PartitionInfo] = []
        space_entries: list[tuple[str, SpaceInfo]] = []
        children = dev.get("children")

        if isinstance(children, list) and children:
            for child_index, child in enumerate(children, start=1):
                if not isinstance(child, dict):
                    raise InspectionParseError("lsblk partition child entry is not a mapping.")
                part, part_spaces = self._parse_partition(
                    child,
                    parent_scheme=parent_scheme,
                    fallback_index=child_index,
                    df_space=df_space,
                )
                partitions.append(part)
                space_entries.extend(part_spaces)
        elif _clean_str(dev.get("fstype")) or self._extract_mountpoints(dev):
            part, part_spaces = self._parse_partition(
                dev,
                parent_scheme=parent_scheme,
                fallback_index=1,
                df_space=df_space,
            )
            partitions.append(part)
            space_entries.extend(part_spaces)

        return partitions, space_entries

    @staticmethod
    def _deduplicate_space_entries(
        space_entries: list[tuple[str, SpaceInfo]],
    ) -> tuple[tuple[str, SpaceInfo], ...]:
        """Deduplicate space entries by mount point while preserving order."""
        seen_mounts: set[str] = set()
        unique: list[tuple[str, SpaceInfo]] = []
        for mount, space in space_entries:
            if mount not in seen_mounts:
                seen_mounts.add(mount)
                unique.append((mount, space))
        return tuple(unique)

    def _build_drive_inspection(
        self,
        dev: dict[str, Any],
        df_space: dict[str, SpaceInfo],
    ) -> DriveInspection:
        """Construct a DriveInspection record from a blockdevice dictionary."""
        identity = self._build_drive_identity(dev)
        partition_scheme = _clean_str(dev.get("pttype"))
        partitions, space_entries = self._collect_partitions(
            dev,
            parent_scheme=partition_scheme,
            df_space=df_space,
        )
        unique_spaces = self._deduplicate_space_entries(space_entries)
        return DriveInspection(
            drive=identity,
            partitions=tuple(partitions),
            space_by_mount=unique_spaces,
        )

    def _parse_partition(
        self,
        part_dict: dict[str, Any],
        parent_scheme: str | None,
        fallback_index: int,
        df_space: dict[str, SpaceInfo],
    ) -> tuple[PartitionInfo, list[tuple[str, SpaceInfo]]]:
        """Parse one partition entry and collect its associated space metrics."""
        name = _sanitize_device_name(part_dict.get("name"))
        mount_points = self._extract_mountpoints(part_dict)
        partition_info = PartitionInfo(
            name=name,
            path=_sanitize_device_path(part_dict.get("path"), name),
            size_bytes=_clean_int(part_dict.get("size")),
            partition_number=_extract_partition_number(part_dict, fallback_index),
            partition_scheme=_clean_str(part_dict.get("pttype")) or parent_scheme,
            partition_type=_clean_str(part_dict.get("parttype")),
            filesystem=_clean_str(part_dict.get("fstype")),
            label=_clean_str(part_dict.get("label")),
            uuid=_clean_str(part_dict.get("uuid")),
            mount_points=mount_points,
            is_read_only=bool(part_dict.get("ro")),
        )
        spaces = self._resolve_partition_spaces(part_dict, mount_points, df_space)
        return partition_info, spaces

    @staticmethod
    def _resolve_partition_spaces(
        part_dict: dict[str, Any],
        mount_points: tuple[str, ...],
        df_space: dict[str, SpaceInfo],
    ) -> list[tuple[str, SpaceInfo]]:
        """Resolve space metrics for partition mount points from df or lsblk fallback."""
        spaces: list[tuple[str, SpaceInfo]] = []
        for mount in mount_points:
            if mount in df_space:
                spaces.append((mount, df_space[mount]))
            elif part_dict.get("fssize") is not None and part_dict.get("fsavail") is not None:
                total_b = _clean_int(part_dict.get("fssize"))
                avail_b = _clean_int(part_dict.get("fsavail"))
                used_val = part_dict.get("fsused")
                used_b = _clean_int(used_val) if used_val is not None else max(0, total_b - avail_b)
                space_info = SpaceInfo(
                    total_bytes=total_b,
                    used_bytes=used_b,
                    available_bytes=avail_b,
                )
                spaces.append((mount, space_info))
        return spaces

    @staticmethod
    def _extract_mountpoints(device_entry: dict[str, Any]) -> tuple[str, ...]:
        """Extract, sanitize, and deduplicate mount points from a device entry."""
        raw_mounts = device_entry.get("mountpoints")
        mounts: list[str] = []
        if isinstance(raw_mounts, list):
            for m in raw_mounts:
                clean = _sanitize_mount_point(m)
                if clean:
                    mounts.append(clean)
        elif device_entry.get("mountpoint"):
            clean = _sanitize_mount_point(device_entry.get("mountpoint"))
            if clean:
                mounts.append(clean)
        return tuple(dict.fromkeys(mounts))
