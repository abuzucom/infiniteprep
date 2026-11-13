"""Tests for the Windows removable flag source (D188, D372).

Get-Disk reports BusType USB for flash sticks and portable SSDs alike. The
removable flag comes from the Get-Volume DriveType of the disk's volumes.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from typing import Any

from infiniteprep.services.usb.windows import (
    DISK_COMMAND,
    PARTITION_COMMAND,
    VOLUME_COMMAND,
    WindowsDriveInspector,
)

DISK_NUMBER = 2
DISK_SIZE_BYTES = 500_107_862_016
DRIVE_LETTER = "E"


def build_runner(bus_type: str, drive_type: str | None):
    """Return a runner that replays one disk, one lettered partition, and one volume."""
    disk = {
        "DiskNumber": DISK_NUMBER,
        "Model": "Portable SSD",
        "Manufacturer": None,
        "SerialNumber": "TESTSERIAL0002",
        "BusType": bus_type,
        "Size": DISK_SIZE_BYTES,
        "PartitionStyle": "MBR",
        "IsReadOnly": False,
    }
    partition = {
        "DiskNumber": DISK_NUMBER,
        "PartitionNumber": 1,
        "Size": DISK_SIZE_BYTES,
        "Type": "FAT32",
        "GptType": None,
        "DriveLetter": DRIVE_LETTER,
        "IsReadOnly": False,
        "AccessPaths": None,
    }
    volume: dict[str, Any] = {
        "DriveLetter": DRIVE_LETTER,
        "FileSystemLabel": "SSD",
        "FileSystem": "FAT32",
        "Size": DISK_SIZE_BYTES,
        "SizeRemaining": DISK_SIZE_BYTES,
        "DriveType": drive_type,
    }
    outputs = {
        DISK_COMMAND: json.dumps([disk]),
        PARTITION_COMMAND: json.dumps([partition]),
        VOLUME_COMMAND: json.dumps([volume]),
    }

    def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, outputs[tuple(cmd)], "")

    return run


def inspect_one(bus_type: str, drive_type: str | None) -> Any:
    """Inspect the single replayed disk and return its drive identity."""
    inspector = WindowsDriveInspector(build_runner(bus_type, drive_type))
    return inspector.inspect_all()[0].drive


class WindowsRemovableFlagTests(unittest.TestCase):
    """The removable flag follows the volume DriveType, not the bus."""

    def test_usb_disk_with_a_fixed_volume_is_not_removable(self) -> None:
        drive = inspect_one("USB", "Fixed")

        self.assertFalse(drive.is_removable)
        self.assertEqual(drive.bus_type, "USB")

    def test_usb_disk_with_a_removable_volume_is_removable(self) -> None:
        self.assertTrue(inspect_one("USB", "Removable").is_removable)

    def test_usb_disk_without_a_drive_type_is_not_removable(self) -> None:
        self.assertFalse(inspect_one("USB", None).is_removable)

    def test_drive_type_match_ignores_case(self) -> None:
        self.assertTrue(inspect_one("USB", "removable").is_removable)


if __name__ == "__main__":
    unittest.main()
