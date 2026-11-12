"""Read-only Windows preflight probe using PowerShell Storage cmdlets (D131, D138, D181, D187).

The probe runs three fixed projections. No drive data enters command text.
None of the commands needs elevation.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.services.usb._shared import clean_str
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveInspection,
    FilesystemHealth,
)
from infiniteprep.services.usb.preflight import FACT_PRIMARY_PARTITION
from infiniteprep.services.usb.probe_facts import (
    MBR_EXTENDED_TYPE_CODES,
    DriveFacts,
    PartitionFacts,
    apply_drive_facts,
)
from infiniteprep.services.usb.windows import WindowsDriveInspector, _powershell_command

DISK_FACTS_COMMAND: Final[tuple[str, ...]] = _powershell_command(
    "Get-Disk | Select-Object DiskNumber, UniqueId, IsBoot, IsSystem | ConvertTo-Json -Depth 3"
)
PARTITION_FACTS_COMMAND: Final[tuple[str, ...]] = _powershell_command(
    "Get-Partition | Select-Object DiskNumber, PartitionNumber, IsActive, IsBoot, IsSystem, "
    "MbrType, DriveLetter | ConvertTo-Json -Depth 3"
)
VOLUME_HEALTH_COMMAND: Final[tuple[str, ...]] = _powershell_command(
    "Get-Volume | Select-Object DriveLetter, HealthStatus | ConvertTo-Json -Depth 3"
)
# Bounds the disk number that a drive name may carry.
MAX_DISK_NUMBER_DIGITS: Final[int] = 6
DISK_NAME_RE: Final[re.Pattern[str]] = re.compile(rf"^disk(\d{{1,{MAX_DISK_NUMBER_DIGITS}}})$")
# Get-Volume reports HealthStatus as text or as the MSFT_Volume numeric code.
HEALTH_CODE_HEALTHY: Final[str] = "0"
HEALTH_CODE_SCAN_NEEDED: Final[str] = "1"
HEALTH_CODE_SPOT_FIX_NEEDED: Final[str] = "2"
HEALTH_CODE_FULL_REPAIR_NEEDED: Final[str] = "3"
HEALTHY_STATUS_VALUES: Final[frozenset[str]] = frozenset({"healthy", HEALTH_CODE_HEALTHY})
ERROR_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {
        "scan needed",
        "spot fix needed",
        "full repair needed",
        HEALTH_CODE_SCAN_NEEDED,
        HEALTH_CODE_SPOT_FIX_NEEDED,
        HEALTH_CODE_FULL_REPAIR_NEEDED,
    }
)


def _read_flag(entry: dict[str, Any], key: str) -> bool | None:
    """Return a boolean cmdlet property, or None for any other value."""
    value = entry.get(key)
    return value if isinstance(value, bool) else None


def _read_number(entry: dict[str, Any], key: str) -> int | None:
    """Return an integer cmdlet property, or None for any other value."""
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _read_health(value: Any) -> FilesystemHealth | None:
    """Map a HealthStatus value to a health state, or None when unknown."""
    status = (clean_str(value) or "").casefold()
    if status in HEALTHY_STATUS_VALUES:
        return FilesystemHealth.HEALTHY
    if status in ERROR_STATUS_VALUES:
        return FilesystemHealth.ERRORS
    return None


def _read_system_state(disk: dict[str, Any], partitions: list[dict[str, Any]]) -> bool | None:
    """Return True for a boot or system disk, None when the disk flags are absent."""
    disk_flags = (_read_flag(disk, "IsBoot"), _read_flag(disk, "IsSystem"))
    partition_flags = [
        _read_flag(entry, key) for entry in partitions for key in ("IsBoot", "IsSystem")
    ]
    if any(disk_flags) or any(partition_flags):
        return True
    if None in disk_flags:
        return None
    return False


def _read_primary_state(entry: dict[str, Any]) -> bool | None:
    """Return False for an extended container, True for another MBR type, else None."""
    mbr_type = _read_number(entry, "MbrType")
    if mbr_type is None:
        return None
    return mbr_type not in MBR_EXTENDED_TYPE_CODES


class WindowsPreflightProbe:
    """Adds preflight facts to a Windows drive inspection in read-only mode."""

    def __init__(
        self,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """Initialize with an optional command runner for hermetic testing."""
        # The inspector owns the byte-capped runner and the JSON list parser.
        self._inspector = WindowsDriveInspector(command_runner)

    def probe(self, inspection: DriveInspection) -> DriveInspection:
        """Return the inspection with system, active, primary, health, and hardware facts.

        Raises CommandExecutionError when a command fails or the disk is gone.
        Raises InspectionParseError for malformed command output.
        """
        match = DISK_NAME_RE.match(inspection.drive.name)
        if not match:
            raise CommandExecutionError("The drive name is not a Windows disk name. Rescan drives.")
        number = int(match.group(1))
        run = self._inspector._run_and_parse
        disks = [
            entry
            for entry in run(DISK_FACTS_COMMAND, "Get-Disk")
            if entry.get("DiskNumber") == number
        ]
        if not disks:
            raise CommandExecutionError(f"Disk {number} was not found. Reconnect it and rescan.")
        partitions = [
            entry
            for entry in run(PARTITION_FACTS_COMMAND, "Get-Partition")
            if entry.get("DiskNumber") == number
        ]
        health_by_letter = self._index_health(run(VOLUME_HEALTH_COMMAND, "Get-Volume"))
        partition_facts = self._build_partition_facts(partitions, health_by_letter)
        # MbrType cannot tell a logical partition from a primary one.
        inferred = {FACT_PRIMARY_PARTITION} if partition_facts else set()
        facts = DriveFacts(
            is_system=_read_system_state(disks[0], partitions),
            hardware_id=clean_str(disks[0].get("UniqueId")),
            partitions=partition_facts,
            inferred_facts=frozenset(inferred),
        )
        return apply_drive_facts(inspection, facts)

    @staticmethod
    def _index_health(volumes: list[dict[str, Any]]) -> dict[str, FilesystemHealth | None]:
        """Map each uppercase drive letter to its volume health."""
        indexed: dict[str, FilesystemHealth | None] = {}
        for volume in volumes:
            letter = clean_str(volume.get("DriveLetter"))
            if letter:
                indexed.setdefault(letter.upper(), _read_health(volume.get("HealthStatus")))
        return indexed

    @staticmethod
    def _build_partition_facts(
        partitions: list[dict[str, Any]],
        health_by_letter: dict[str, FilesystemHealth | None],
    ) -> dict[int, PartitionFacts]:
        """Build the facts of every partition that carries a valid number."""
        facts: dict[int, PartitionFacts] = {}
        for entry in partitions:
            partition_number = _read_number(entry, "PartitionNumber")
            if partition_number is None or partition_number <= 0:
                continue
            letter = (clean_str(entry.get("DriveLetter")) or "").upper()
            facts[partition_number] = PartitionFacts(
                is_active=_read_flag(entry, "IsActive"),
                is_primary=_read_primary_state(entry),
                filesystem_health=health_by_letter.get(letter),
            )
        return facts
