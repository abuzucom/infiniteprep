"""Read-only macOS preflight probe using diskutil info (D93, D131, D203).

The probe needs no raw device access. diskutil exposes no MBR active flag,
no primary flag, and no filesystem health. Those facts stay unverified here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from infiniteprep.services.usb._shared import clean_str
from infiniteprep.services.usb.macos import MACOS_DEVICE_SAFE_RE, MacOSDriveInspector
from infiniteprep.services.usb.models import CommandExecutionError, DriveInspection
from infiniteprep.services.usb.probe_facts import DriveFacts, apply_drive_facts

ROOT_MOUNT_POINT: Final[str] = "/"
SYSTEM_VOLUME_PREFIX: Final[str] = "/System/Volumes/"
HARDWARE_ID_KEYS: Final[tuple[str, ...]] = ("MediaName", "IORegistryEntryName")

InfoReader = Callable[[str], dict[str, Any]]


def _read_default_info(identifier: str) -> dict[str, Any]:
    """Run the fixed diskutil info command through the bounded inspector runner."""
    return MacOSDriveInspector()._run_info_plist(identifier)


def _is_system_mount(mount_point: str) -> bool:
    """Return True for the root volume and the system volume group mounts."""
    return mount_point == ROOT_MOUNT_POINT or mount_point.startswith(SYSTEM_VOLUME_PREFIX)


class MacOSPreflightProbe:
    """Adds preflight facts to a macOS drive inspection in read-only mode."""

    def __init__(self, info_reader: InfoReader | None = None) -> None:
        """Initialize with an optional diskutil info source for hermetic testing."""
        self._info_reader = info_reader or _read_default_info

    def probe(self, inspection: DriveInspection) -> DriveInspection:
        """Return the inspection with the system state and the hardware ID.

        The inspector attaches APFS volumes to their physical disk. A system
        mount on any partition therefore marks the whole drive.
        Raises CommandExecutionError for an unsafe name or a failed command.
        Raises InspectionParseError for malformed command output.
        """
        identifier = inspection.drive.name
        if not MACOS_DEVICE_SAFE_RE.match(identifier):
            raise CommandExecutionError("The drive name is not a macOS disk name. Rescan drives.")
        info = self._info_reader(identifier)
        holds_system_mount = any(
            _is_system_mount(mount_point)
            for partition in inspection.partitions
            for mount_point in partition.mount_points
        )
        hardware_names = [clean_str(info.get(key)) for key in HARDWARE_ID_KEYS]
        facts = DriveFacts(
            is_system=holds_system_mount or info.get("SystemImage") is True,
            hardware_id=next((name for name in hardware_names if name), None),
        )
        return apply_drive_facts(inspection, facts)
