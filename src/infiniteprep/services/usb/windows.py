"""Read-only Windows drive inspection using PowerShell Storage cmdlets (D93, D161, D181, Rule 1).

This module executes fixed argument arrays without shell interpolation to inspect
connected storage disks, partition layouts, volumes, and available space.
It performs strictly read-only queries and treats all drive-supplied metadata as
untrusted input. No drive data is ever placed in command text.

The interpreter is Windows PowerShell 5.1 on Windows 10/11. The -Command input
is not gated by the ExecutionPolicy (only script files are), so no policy flag
is passed. -NoProfile and -NonInteractive keep profiles and prompts out.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.services.usb._shared import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    clean_int,
    clean_str,
)
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveIdentity,
    DriveInspection,
    InspectionParseError,
    PartitionInfo,
    SpaceInfo,
)

# The interpreter path is derived from SystemRoot so a relocated system root
# still resolves, and pinned so a hostile PATH entry cannot substitute the
# interpreter (review findings M18/M10, PR #196).
_SYSTEM_ROOT: Final[str] = os.environ.get("SYSTEMROOT") or "C:\\Windows"
POWERSHELL_EXE: Final[str] = _SYSTEM_ROOT + r"\System32\WindowsPowerShell\v1.0\powershell.exe"
# Bounded output keeps a malfunctioning storage stack from exhausting memory
# during parsing (foucault MEDIUM, PR #196).
MAX_COMMAND_OUTPUT_BYTES: Final[int] = 8 * 1024 * 1024
MAX_ERROR_TEXT_LEN: Final[int] = 4096
DISK_COMMAND_TEXT: Final[str] = (
    "Get-Disk | Select-Object DiskNumber, Model, Manufacturer, "
    "SerialNumber, BusType, Size, PartitionStyle, IsReadOnly "
    "| ConvertTo-Json -Depth 3"
)
PARTITION_COMMAND_TEXT: Final[str] = (
    "Get-Partition | Select-Object DiskNumber, PartitionNumber, Size, Type, GptType, "
    "DriveLetter, IsReadOnly, AccessPaths | ConvertTo-Json -Depth 3"
)
VOLUME_COMMAND_TEXT: Final[str] = (
    "Get-Volume | Select-Object DriveLetter, FileSystemLabel, FileSystem, Size, "
    "SizeRemaining, DriveType | ConvertTo-Json -Depth 3"
)


def _powershell_command(script_text: str) -> tuple[str, ...]:
    """Build the fixed argument array for one PowerShell Storage projection."""
    return (POWERSHELL_EXE, "-NoProfile", "-NonInteractive", "-Command", script_text)


DISK_COMMAND: Final[tuple[str, ...]] = _powershell_command(DISK_COMMAND_TEXT)
PARTITION_COMMAND: Final[tuple[str, ...]] = _powershell_command(PARTITION_COMMAND_TEXT)
VOLUME_COMMAND: Final[tuple[str, ...]] = _powershell_command(VOLUME_COMMAND_TEXT)
_BACKSLASH: Final[str] = chr(92)
_PHYSICAL_DRIVE_PREFIX: Final[str] = _BACKSLASH * 2 + "." + _BACKSLASH + "PHYSICALDRIVE"
DISK_TARGET_RE: Final[re.Pattern[str]] = re.compile(r"^disk(\d+)$", re.IGNORECASE)
PHYSICAL_DRIVE_PATH_RE: Final[re.Pattern[str]] = re.compile(
    re.escape(_PHYSICAL_DRIVE_PREFIX) + r"(\d+)$", re.IGNORECASE
)
DISK_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"^\d+$")
DRIVE_LETTER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]$")
GUID_SHAPE_RE: Final[str] = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
GUID_SAFE_RE: Final[re.Pattern[str]] = re.compile(r"^" + GUID_SHAPE_RE + r"$")
_VOLUME_ACCESS_PREFIX: Final[str] = _BACKSLASH * 2 + "?" + _BACKSLASH + "Volume{"
_VOLUME_ACCESS_SUFFIX: Final[str] = "}" + _BACKSLASH
VOLUME_ACCESS_PATH_RE: Final[re.Pattern[str]] = re.compile(
    re.escape(_VOLUME_ACCESS_PREFIX)
    + r"("
    + GUID_SHAPE_RE
    + r")"
    + re.escape(_VOLUME_ACCESS_SUFFIX)
    + r"$"
)


def _sanitize_drive_letter(val: Any) -> str | None:
    """Validate a single drive letter from untrusted output, uppercased."""
    cleaned = clean_str(val)
    if not cleaned or not DRIVE_LETTER_RE.match(cleaned):
        return None
    return cleaned.upper()


def _extract_guid(raw: Any) -> str | None:
    """Validate a GUID with optional braces from untrusted output, lowercased."""
    cleaned = clean_str(raw)
    if not cleaned:
        return None
    guid = cleaned.strip("{}")
    if not GUID_SAFE_RE.match(guid):
        return None
    return guid.lower()


def _extract_volume_guids(access_paths: Any) -> tuple[str, ...]:
    """Extract volume GUIDs from AccessPaths entries, rejecting anything else."""
    if not isinstance(access_paths, list):
        return ()
    guids: list[str] = []
    for entry in access_paths:
        match = VOLUME_ACCESS_PATH_RE.match(clean_str(entry) or "")
        if match and match.group(1).lower() not in guids:
            guids.append(match.group(1).lower())
    return tuple(guids)


def _require_disk_number(entry: dict[str, Any], label: str) -> int:
    """Return a valid non-negative DiskNumber or raise a parse error."""
    value = entry.get("DiskNumber")
    if value is None:
        raise InspectionParseError(f"{label} entry is missing 'DiskNumber'.")
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise InspectionParseError(f"{label} 'DiskNumber' is not an integer.") from None
    if number < 0:
        raise InspectionParseError(f"{label} 'DiskNumber' is negative.")
    return number


def _require_partition_number(entry: dict[str, Any]) -> int:
    """Return a valid positive PartitionNumber or raise a parse error."""
    value = entry.get("PartitionNumber")
    if value is None:
        raise InspectionParseError("Get-Partition entry is missing 'PartitionNumber'.")
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise InspectionParseError("Get-Partition 'PartitionNumber' is not an integer.") from None
    if number <= 0:
        raise InspectionParseError("Get-Partition 'PartitionNumber' is not positive.")
    return number


def _space_from_volume(volume: dict[str, Any]) -> SpaceInfo | None:
    """Build SpaceInfo from Get-Volume size fields, or None when unavailable."""
    if volume.get("Size") is None:
        return None
    total_b = clean_int(volume.get("Size"))
    remaining = volume.get("SizeRemaining")
    used = volume.get("SizeUsed")
    if remaining is not None:
        avail_b = clean_int(remaining)
    elif used is not None:
        avail_b = max(0, total_b - clean_int(used))
    else:
        return None
    return SpaceInfo(
        total_bytes=total_b,
        used_bytes=max(0, total_b - avail_b),
        available_bytes=avail_b,
    )


def _is_removable_disk(bus_type: str | None, partition_volumes: list[dict[str, Any]]) -> bool:
    """Infer removability from bus type or any partition volume drive type."""
    if (bus_type or "").casefold() == "usb":
        return True
    return any(
        (clean_str(volume.get("DriveType")) or "").casefold() == "removable"
        for volume in partition_volumes
    )


def _build_drive_identity(disk: dict[str, Any], number: int, removable: bool) -> DriveIdentity:
    """Construct a sanitized DriveIdentity from a Get-Disk dictionary."""
    return DriveIdentity(
        name=f"disk{number}",
        path=f"\\\\.\\PHYSICALDRIVE{number}",
        size_bytes=clean_int(disk.get("Size")),
        model=clean_str(disk.get("Model")),
        vendor=clean_str(disk.get("Manufacturer")),
        serial=clean_str(disk.get("SerialNumber")),
        is_removable=removable,
        is_read_only=bool(disk.get("IsReadOnly")),
        bus_type=clean_str(disk.get("BusType")),
    )


def _decode_capped(stdout: bytes, stderr: bytes) -> tuple[str, str]:
    """Decode raw command output only after enforcing the byte caps."""
    if len(stdout) > MAX_COMMAND_OUTPUT_BYTES:
        message = f"command output exceeds the {MAX_COMMAND_OUTPUT_BYTES} byte limit."
        raise InspectionParseError(message)
    if len(stderr) > MAX_COMMAND_OUTPUT_BYTES:
        message = f"command error output exceeds the {MAX_COMMAND_OUTPUT_BYTES} byte limit."
        raise InspectionParseError(message)
    return (
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _default_windows_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the fixed command with byte-capped output decoded lossily."""
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=False,
        check=False,
        timeout=DEFAULT_COMMAND_TIMEOUT_SECONDS,
        shell=False,
    )
    stdout, stderr = _decode_capped(completed.stdout, completed.stderr)
    return subprocess.CompletedProcess(cmd, completed.returncode, stdout, stderr)


class WindowsDriveInspector:
    """Inspects Windows disks, partitions, volumes, and free space in read-only mode."""

    def __init__(
        self,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """Initialize with optional command runner for hermetic testing."""
        self._command_runner = command_runner or _default_windows_runner

    def inspect_all(self) -> tuple[DriveInspection, ...]:
        """Inspect all disks and return DriveInspection records ordered by disk number."""
        disks = self._run_and_parse(DISK_COMMAND, "Get-Disk")
        partitions = self._run_and_parse(PARTITION_COMMAND, "Get-Partition")
        volumes = self._run_and_parse(VOLUME_COMMAND, "Get-Volume")
        partitions_by_disk = self._index_partitions(partitions)
        volume_by_letter = self._index_volumes(volumes)
        inspections: list[DriveInspection] = []
        for disk in sorted(disks, key=lambda d: _require_disk_number(d, "Get-Disk")):
            number = _require_disk_number(disk, "Get-Disk")
            inspections.append(
                self._build_drive_inspection(disk, number, partitions_by_disk, volume_by_letter)
            )
        return tuple(inspections)

    def inspect_device(self, device_name_or_path: str) -> DriveInspection:
        """Inspect one disk by name ('disk5'), number ('5'), or PHYSICALDRIVE path.

        The Storage cmdlets are machine-wide, so every call enumerates all
        disks and returns the matching inspection.
        """
        target = clean_str(device_name_or_path)
        if not target or ".." in target:
            raise CommandExecutionError(f"Device '{device_name_or_path}' is invalid or unsafe.")
        number = self._parse_target_number(target)
        if number is None:
            raise CommandExecutionError(f"Device '{device_name_or_path}' is invalid or unsafe.")
        for inspection in self.inspect_all():
            if inspection.drive.name == f"disk{number}":
                return inspection
        raise CommandExecutionError(f"Device '{device_name_or_path}' was not found.")

    @staticmethod
    def _parse_target_number(target: str) -> int | None:
        """Extract the disk number from a validated target form, or None."""
        if DISK_NUMBER_RE.match(target):
            return int(target)
        match = DISK_TARGET_RE.match(target) or PHYSICAL_DRIVE_PATH_RE.match(target)
        if not match:
            return None
        return int(match.group(1))

    def _run_and_parse(self, command: tuple[str, ...], label: str) -> list[dict[str, Any]]:
        """Run a fixed command and parse its JSON list of mappings."""
        try:
            completed = self._command_runner(list(command))
        except (subprocess.SubprocessError, OSError) as exc:
            raise CommandExecutionError(f"{label} execution failed: {exc}") from exc

        stdout = completed.stdout
        if len(stdout) > MAX_COMMAND_OUTPUT_BYTES:
            message = f"{label} output exceeds the {MAX_COMMAND_OUTPUT_BYTES} byte limit."
            raise InspectionParseError(message)

        if completed.returncode != 0:
            raw_err = completed.stderr.strip()[:MAX_ERROR_TEXT_LEN]
            err = raw_err or f"exit code {completed.returncode}"
            raise CommandExecutionError(f"{label} failed with {err}"[:MAX_ERROR_TEXT_LEN])

        stripped = stdout.strip()
        if not stripped:
            return []

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise InspectionParseError(f"{label} returned malformed JSON: {exc}") from exc

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise InspectionParseError(f"{label} output is not a list or object.")

        for entry in data:
            if not isinstance(entry, dict):
                raise InspectionParseError(f"{label} entry is not a mapping.")

        return data

    @staticmethod
    def _index_partitions(
        partitions: list[dict[str, Any]],
    ) -> dict[int, list[dict[str, Any]]]:
        """Group partition entries by disk number, ordered by partition number."""
        grouped: dict[int, list[dict[str, Any]]] = {}
        for partition in partitions:
            number = _require_disk_number(partition, "Get-Partition")
            grouped.setdefault(number, []).append(partition)
        for entries in grouped.values():
            entries.sort(key=_require_partition_number)
        return grouped

    @staticmethod
    def _index_volumes(volumes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Map uppercase drive letter to the first matching volume entry."""
        indexed: dict[str, dict[str, Any]] = {}
        for volume in volumes:
            letter = _sanitize_drive_letter(volume.get("DriveLetter"))
            if letter and letter not in indexed:
                indexed[letter] = volume
        return indexed

    def _build_drive_inspection(
        self,
        disk: dict[str, Any],
        number: int,
        partitions_by_disk: dict[int, list[dict[str, Any]]],
        volume_by_letter: dict[str, dict[str, Any]],
    ) -> DriveInspection:
        """Construct a DriveInspection record for one disk."""
        partition_entries = partitions_by_disk.get(number, [])
        partition_volumes = self._collect_partition_volumes(partition_entries, volume_by_letter)
        identity = _build_drive_identity(
            disk, number, _is_removable_disk(clean_str(disk.get("BusType")), partition_volumes)
        )
        scheme_raw = clean_str(disk.get("PartitionStyle"))
        scheme = scheme_raw.casefold() if scheme_raw else None
        partitions: list[PartitionInfo] = []
        space_entries: list[tuple[str, SpaceInfo]] = []
        for entry in partition_entries:
            partition, spaces = self._build_partition(entry, number, scheme, volume_by_letter)
            partitions.append(partition)
            space_entries.extend(spaces)
        return DriveInspection(
            drive=identity,
            partitions=tuple(partitions),
            space_by_mount=self._deduplicate_space_entries(space_entries),
        )

    @staticmethod
    def _collect_partition_volumes(
        partition_entries: list[dict[str, Any]],
        volume_by_letter: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Collect the volume entries attached to a disk's lettered partitions."""
        volumes: list[dict[str, Any]] = []
        for entry in partition_entries:
            letter = _sanitize_drive_letter(entry.get("DriveLetter"))
            if letter and letter in volume_by_letter:
                volumes.append(volume_by_letter[letter])
        return volumes

    def _build_partition(
        self,
        entry: dict[str, Any],
        disk_number: int,
        scheme: str | None,
        volume_by_letter: dict[str, dict[str, Any]],
    ) -> tuple[PartitionInfo, list[tuple[str, SpaceInfo]]]:
        """Build one PartitionInfo plus its space entries from a Get-Partition entry."""
        partition_number = _require_partition_number(entry)
        letter = _sanitize_drive_letter(entry.get("DriveLetter"))
        volume = volume_by_letter.get(letter, {}) if letter else {}
        guids = _extract_volume_guids(entry.get("AccessPaths"))
        uuid = guids[0] if guids else None
        path = f"\\\\.\\Volume{{{uuid}}}" if uuid else f"disk{disk_number}part{partition_number}"
        spaces: list[tuple[str, SpaceInfo]] = []
        if letter:
            space = _space_from_volume(volume)
            if space is not None:
                spaces.append((f"{letter}:\\", space))
        partition_type = _extract_guid(entry.get("GptType")) or clean_str(entry.get("Type"))
        return (
            PartitionInfo(
                name=f"disk{disk_number}part{partition_number}",
                path=path,
                size_bytes=clean_int(entry.get("Size")),
                partition_number=partition_number,
                partition_scheme=scheme,
                partition_type=partition_type,
                filesystem=clean_str(volume.get("FileSystem")) if volume else None,
                label=clean_str(volume.get("FileSystemLabel")) if volume else None,
                uuid=uuid,
                mount_points=(f"{letter}:\\",) if letter else (),
                is_read_only=bool(entry.get("IsReadOnly")),
            ),
            spaces,
        )

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
