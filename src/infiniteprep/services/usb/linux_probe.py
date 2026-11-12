"""Read-only Linux preflight probe using lsblk, udevadm, and the mount table (D131, D183, D187).

The probe needs no raw device access. Filesystem health comes from a passive
source and is marked inferred. A mounted FAT volume that the kernel switched
to read-only after an error counts as a filesystem error.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from infiniteprep.services.usb._shared import clean_str, default_command_runner
from infiniteprep.services.usb.linux import _decode_df_mount
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveInspection,
    FilesystemHealth,
    InspectionParseError,
    PartitionInfo,
)
from infiniteprep.services.usb.preflight import FACT_FILESYSTEM_HEALTH
from infiniteprep.services.usb.probe_facts import (
    MBR_ACTIVE_FLAG,
    DriveFacts,
    PartitionFacts,
    apply_drive_facts,
    classify_mbr_partition,
)

logger = logging.getLogger(__name__)

LSBLK_FACTS_COMMAND: Final[tuple[str, ...]] = (
    "lsblk",
    "-J",
    "-b",
    "-o",
    "NAME,TYPE,PARTN,PARTFLAGS,PARTTYPE,MOUNTPOINTS",
)
UDEVADM_COMMAND_PREFIX: Final[tuple[str, ...]] = ("udevadm", "info", "--query=property", "--name")
MOUNT_TABLE_PATH: Final[Path] = Path("/proc/self/mounts")
SYSTEM_MOUNT_POINTS: Final[frozenset[str]] = frozenset(
    {"/", "/boot", "/boot/efi", "/efi", "[SWAP]"}
)
# Bounds the device tree walk against malformed or hostile lsblk output.
MAX_DEVICE_TREE_NODES: Final[int] = 4096
MOUNT_TABLE_FIELD_COUNT: Final[int] = 4
READ_ONLY_MOUNT_OPTION: Final[str] = "ro"
HEX_BASE: Final[int] = 16
# Linux limits one path component to 255 bytes.
MAX_DEVICE_PATH_SUFFIX_LENGTH: Final[int] = 255
# A USB vendor or product ID holds four hexadecimal digits.
USB_ID_HEX_DIGITS: Final[int] = 4
DEVICE_PATH_RE: Final[re.Pattern[str]] = re.compile(
    rf"^/dev/[A-Za-z0-9_\-./]{{1,{MAX_DEVICE_PATH_SUFFIX_LENGTH}}}$"
)
USB_ID_RE: Final[re.Pattern[str]] = re.compile(rf"^[0-9a-fA-F]{{{USB_ID_HEX_DIGITS}}}$")

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def read_mount_table() -> str:
    """Return the kernel mount table of the current process."""
    return MOUNT_TABLE_PATH.read_text(encoding="utf-8", errors="replace")


def _parse_hex(value: Any) -> int | None:
    """Return the integer of a hexadecimal lsblk field such as 0x80, or None."""
    text = clean_str(value)
    if not text:
        return None
    try:
        return int(text, HEX_BASE)
    except ValueError:
        return None


def _parse_partition_number(value: Any) -> int | None:
    """Return a positive partition number from an lsblk field, or None."""
    text = clean_str(value)
    if not text or not text.isdigit():
        return None
    return int(text) or None


def _collect_mount_points(device: dict[str, Any]) -> set[str]:
    """Return every mount point below a device, walking children iteratively."""
    mount_points: set[str] = set()
    pending = [device]
    visited = 0
    while pending and visited < MAX_DEVICE_TREE_NODES:
        node = pending.pop()
        visited += 1
        raw_mounts = node.get("mountpoints")
        if isinstance(raw_mounts, list):
            mount_points.update(str(mount) for mount in raw_mounts if mount)
        children = node.get("children")
        if isinstance(children, list):
            pending.extend(child for child in children if isinstance(child, dict))
    return mount_points


def _read_read_only_mounts(mount_table: str) -> dict[str, bool]:
    """Map each mount point of a mount table to its read-only state."""
    states: dict[str, bool] = {}
    for line in mount_table.splitlines():
        fields = line.split()
        if len(fields) < MOUNT_TABLE_FIELD_COUNT:
            continue
        mount_point = _decode_df_mount(fields[1])
        states[mount_point] = READ_ONLY_MOUNT_OPTION in fields[3].split(",")
    return states


class LinuxPreflightProbe:
    """Adds preflight facts to a Linux drive inspection in read-only mode."""

    def __init__(
        self,
        command_runner: CommandRunner | None = None,
        mount_table_reader: Callable[[], str] = read_mount_table,
    ) -> None:
        """Initialize with optional command and mount table sources for hermetic testing."""
        self._command_runner = command_runner or default_command_runner
        self._mount_table_reader = mount_table_reader

    def probe(self, inspection: DriveInspection) -> DriveInspection:
        """Return the inspection with system, active, primary, health, and hardware facts.

        Raises CommandExecutionError when lsblk fails or the drive is gone.
        Raises InspectionParseError for malformed lsblk output.
        """
        device = self._find_device(inspection.drive.name)
        health = self._read_passive_health(inspection.partitions)
        partition_facts: dict[int, PartitionFacts] = {}
        for child in device.get("children") or []:
            if not isinstance(child, dict):
                continue
            number = _parse_partition_number(child.get("partn"))
            if number is None:
                continue
            flags = _parse_hex(child.get("partflags")) or 0
            partition_facts[number] = PartitionFacts(
                is_active=bool(flags & MBR_ACTIVE_FLAG),
                is_primary=classify_mbr_partition(number, _parse_hex(child.get("parttype"))),
                filesystem_health=health.get(number),
            )
        facts = DriveFacts(
            is_system=bool(_collect_mount_points(device) & SYSTEM_MOUNT_POINTS),
            hardware_id=self._read_hardware_id(inspection.drive.path),
            partitions=partition_facts,
            inferred_facts=frozenset({FACT_FILESYSTEM_HEALTH}) if health else frozenset(),
        )
        return apply_drive_facts(inspection, facts)

    def _find_device(self, drive_name: str) -> dict[str, Any]:
        """Run lsblk and return the top-level entry of one drive."""
        try:
            completed = self._command_runner(list(LSBLK_FACTS_COMMAND))
        except (subprocess.SubprocessError, OSError) as exc:
            raise CommandExecutionError(f"lsblk execution failed: {exc}") from exc
        if completed.returncode != 0:
            raise CommandExecutionError(f"lsblk failed with exit code {completed.returncode}.")
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise InspectionParseError(f"lsblk returned malformed JSON: {exc}") from exc
        devices = data.get("blockdevices") if isinstance(data, dict) else None
        if not isinstance(devices, list):
            raise InspectionParseError("lsblk output missing 'blockdevices' list.")
        for device in devices:
            if isinstance(device, dict) and device.get("name") == drive_name:
                return device
        raise CommandExecutionError("The drive was not found. Reconnect it and rescan.")

    def _read_passive_health(
        self, partitions: tuple[PartitionInfo, ...]
    ) -> dict[int, FilesystemHealth]:
        """Infer health per partition number from the kernel mount state."""
        try:
            read_only_mounts = _read_read_only_mounts(self._mount_table_reader())
        except OSError as exc:
            # Health stays unverified. The preflight lists the fact as not verified.
            logger.warning("Mount table read failed: %s", type(exc).__name__)
            return {}
        health: dict[int, FilesystemHealth] = {}
        for partition in partitions:
            if not partition.mount_points or not partition.partition_number:
                continue
            mounted_read_only = read_only_mounts.get(partition.mount_points[0])
            if mounted_read_only is None:
                continue
            # A read-only mount on writable media means the kernel hit an error.
            has_errors = mounted_read_only and not partition.is_read_only
            health[partition.partition_number] = (
                FilesystemHealth.ERRORS if has_errors else FilesystemHealth.HEALTHY
            )
        return health

    def _read_hardware_id(self, device_path: str) -> str | None:
        """Return the USB vendor and product ID pair of a drive, or None."""
        if ".." in device_path or not DEVICE_PATH_RE.match(device_path):
            return None
        try:
            completed = self._command_runner([*UDEVADM_COMMAND_PREFIX, device_path])
        except (subprocess.SubprocessError, OSError) as exc:
            # The hardware ID is optional display and identity data.
            logger.warning("udevadm execution failed: %s", type(exc).__name__)
            return None
        if completed.returncode != 0:
            return None
        properties = dict(
            line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
        )
        vendor_id = properties.get("ID_VENDOR_ID", "")
        product_id = properties.get("ID_MODEL_ID", "")
        if not USB_ID_RE.match(vendor_id) or not USB_ID_RE.match(product_id):
            return None
        return f"{vendor_id.lower()}:{product_id.lower()}"
