"""Unit tests for read-only Linux USB drive and filesystem inspection (D161, D183, Rule 1)."""

from __future__ import annotations

import json
import subprocess
import unittest
from typing import Final

from infiniteprep.services.usb.linux import (
    DF_COMMAND,
    LSBLK_COMMAND,
    LinuxDriveInspector,
)
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveInspection,
    InspectionParseError,
    SpaceInfo,
)

FAT32_STICK_LSBLK_JSON: Final[str] = json.dumps(
    {
        "blockdevices": [
            {
                "name": "sdb",
                "path": "/dev/sdb",
                "model": "Ultra USB 3.0",
                "vendor": "SanDisk",
                "serial": "0401568289410948",
                "rm": True,
                "ro": False,
                "type": "disk",
                "fstype": None,
                "label": None,
                "uuid": None,
                "fsavail": None,
                "fsused": None,
                "fssize": None,
                "mountpoints": [None],
                "pttype": "dos",
                "parttype": None,
                "size": 32010928128,
                "children": [
                    {
                        "name": "sdb1",
                        "path": "/dev/sdb1",
                        "model": None,
                        "vendor": None,
                        "serial": None,
                        "rm": True,
                        "ro": False,
                        "type": "part",
                        "fstype": "vfat",
                        "label": "DJ_SET_1",
                        "uuid": "7B42-19E4",
                        "fsavail": 30541987840,
                        "fsused": 1468940288,
                        "fssize": 32010928128,
                        "mountpoints": ["/media/user/DJ_SET_1"],
                        "pttype": "dos",
                        "parttype": "0xc",
                        "size": 32010928128,
                    }
                ],
            }
        ]
    }
)

FAT32_STICK_DF_OUTPUT: Final[str] = (
    "Filesystem     1-blocks       Used  Available Capacity Mounted on\n"
    "/dev/sdb1   32010928128 1468940288 30541987840       5% /media/user/DJ_SET_1\n"
)

SYSTEM_DISK_LSBLK_JSON: Final[str] = json.dumps(
    {
        "blockdevices": [
            {
                "name": "nvme0n1",
                "path": "/dev/nvme0n1",
                "model": "Samsung SSD 980 PRO 1TB",
                "vendor": "Samsung",
                "serial": "S5GXNF0R123456",
                "rm": False,
                "ro": False,
                "type": "disk",
                "fstype": None,
                "label": None,
                "uuid": None,
                "fsavail": None,
                "fsused": None,
                "fssize": None,
                "mountpoints": [None],
                "pttype": "gpt",
                "parttype": None,
                "size": 1000204886016,
                "children": [
                    {
                        "name": "nvme0n1p1",
                        "path": "/dev/nvme0n1p1",
                        "model": None,
                        "vendor": None,
                        "serial": None,
                        "rm": False,
                        "ro": False,
                        "type": "part",
                        "fstype": "vfat",
                        "label": "SYSTEM_BOOT",
                        "uuid": "1A2B-3C4D",
                        "fsavail": 524288000,
                        "fsused": 104857600,
                        "fssize": 629145600,
                        "mountpoints": ["/boot/efi"],
                        "pttype": "gpt",
                        "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                        "size": 629145600,
                    },
                    {
                        "name": "nvme0n1p2",
                        "path": "/dev/nvme0n1p2",
                        "model": None,
                        "vendor": None,
                        "serial": None,
                        "rm": False,
                        "ro": False,
                        "type": "part",
                        "fstype": "ext4",
                        "label": "ROOT_FS",
                        "uuid": "8a31e892-7f21-4f24-9b55-c992d9f3bfd1",
                        "fsavail": 420000000000,
                        "fsused": 500000000000,
                        "fssize": 950000000000,
                        "mountpoints": ["/"],
                        "pttype": "gpt",
                        "parttype": "0fc63daf-8483-4772-8e79-3d69d8477de4",
                        "size": 950000000000,
                    },
                    {
                        "name": "nvme0n1p3",
                        "path": "/dev/nvme0n1p3",
                        "model": None,
                        "vendor": None,
                        "serial": None,
                        "rm": False,
                        "ro": False,
                        "type": "part",
                        "fstype": "swap",
                        "label": None,
                        "uuid": "4c92b771-3341-419a-9e12-c518b0213d28",
                        "fsavail": None,
                        "fsused": None,
                        "fssize": None,
                        "mountpoints": [None],
                        "pttype": "gpt",
                        "parttype": "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f",
                        "size": 49575740416,
                    },
                ],
            }
        ]
    }
)

SYSTEM_DISK_DF_OUTPUT: Final[str] = (
    "Filesystem       1-blocks         Used    Available Capacity Mounted on\n"
    "/dev/nvme0n1p1   629145600    104857600    524288000      17% /boot/efi\n"
    "/dev/nvme0n1p2 950000000000 500000000000 420000000000      55% /\n"
)

EXFAT_STICK_LSBLK_JSON: Final[str] = json.dumps(
    {
        "blockdevices": [
            {
                "name": "sdc",
                "path": "/dev/sdc",
                "model": "Extreme PRO",
                "vendor": "SanDisk",
                "serial": "AA01092837465012",
                "rm": True,
                "ro": False,
                "type": "disk",
                "fstype": None,
                "label": None,
                "uuid": None,
                "fsavail": None,
                "fsused": None,
                "fssize": None,
                "mountpoints": [None],
                "pttype": "dos",
                "parttype": None,
                "size": 128035676160,
                "children": [
                    {
                        "name": "sdc1",
                        "path": "/dev/sdc1",
                        "model": None,
                        "vendor": None,
                        "serial": None,
                        "rm": True,
                        "ro": False,
                        "type": "part",
                        "fstype": "exfat",
                        "label": "EXFAT_MUSIC",
                        "uuid": "5A3C-1F2E",
                        "fsavail": 120000000000,
                        "fsused": 8035676160,
                        "fssize": 128035676160,
                        "mountpoints": ["/media/user/EXFAT_MUSIC"],
                        "pttype": "dos",
                        "parttype": "0x7",
                        "size": 128035676160,
                    }
                ],
            }
        ]
    }
)

EXFAT_STICK_DF_OUTPUT: Final[str] = (
    "Filesystem     1-blocks       Used     Available Capacity Mounted on\n"
    "/dev/sdc1  128035676160 8035676160  120000000000       7% /media/user/EXFAT_MUSIC\n"
)

EXFAT_GPT_SSD_LSBLK_JSON: Final[str] = json.dumps(
    {
        "blockdevices": [
            {
                "name": "sdd",
                "path": "/dev/sdd",
                "model": "T7 Shield",
                "vendor": "Samsung",
                "serial": "S5T7NS0R998877",
                "rm": True,
                "ro": False,
                "type": "disk",
                "fstype": None,
                "label": None,
                "uuid": None,
                "fsavail": None,
                "fsused": None,
                "fssize": None,
                "mountpoints": [None],
                "pttype": "gpt",
                "parttype": None,
                "size": 2000398934016,
                "children": [
                    {
                        "name": "sdd1",
                        "path": "/dev/sdd1",
                        "model": None,
                        "vendor": None,
                        "serial": None,
                        "rm": True,
                        "ro": False,
                        "type": "part",
                        "fstype": "exfat",
                        "label": "T7_DJ_ARCHIVE",
                        "uuid": "4D2E-99AA",
                        "fsavail": 1500000000000,
                        "fsused": 500398934016,
                        "fssize": 2000398934016,
                        "mountpoints": ["/media/user/T7_DJ_ARCHIVE"],
                        "pttype": "gpt",
                        "parttype": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
                        "size": 2000398934016,
                    }
                ],
            }
        ]
    }
)

EXFAT_GPT_SSD_DF_OUTPUT: Final[str] = (
    "Filesystem          1-blocks         Used     Available Capacity Mounted on\n"
    "/dev/sdd1      2000398934016 500398934016 1500000000000      26% /media/user/T7_DJ_ARCHIVE\n"
)


def _make_mock_runner(
    lsblk_stdout: str = "{}",
    lsblk_returncode: int = 0,
    df_stdout: str = "",
    df_returncode: int = 0,
) -> tuple[list[list[str]], LinuxDriveInspector]:
    """Create an inspector with an intercepted command execution list."""
    executed_commands: list[list[str]] = []

    def mock_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        executed_commands.append(list(cmd))
        if cmd == list(LSBLK_COMMAND):
            return subprocess.CompletedProcess(
                cmd,
                returncode=lsblk_returncode,
                stdout=lsblk_stdout,
                stderr="lsblk mock error" if lsblk_returncode != 0 else "",
            )
        if cmd == list(DF_COMMAND):
            return subprocess.CompletedProcess(
                cmd,
                returncode=df_returncode,
                stdout=df_stdout,
                stderr="df mock error" if df_returncode != 0 else "",
            )
        raise AssertionError(f"Unexpected command executed: {cmd}")

    inspector = LinuxDriveInspector(command_runner=mock_runner)
    return executed_commands, inspector


class TestUsbLinuxInspection(unittest.TestCase):
    """Test suite for Linux drive inspection parsing, free space correlation, and error handling."""

    def test_inspect_fat32_stick(self) -> None:
        """Inspector correctly extracts identity, partition, filesystem, and free space for a FAT32 stick."""
        executed, inspector = _make_mock_runner(
            lsblk_stdout=FAT32_STICK_LSBLK_JSON,
            df_stdout=FAT32_STICK_DF_OUTPUT,
        )

        inspections = inspector.inspect_all()

        self.assertEqual(len(inspections), 1)
        report: DriveInspection = inspections[0]

        # Verify Drive Identity
        self.assertEqual(report.drive.name, "sdb")
        self.assertEqual(report.drive.path, "/dev/sdb")
        self.assertEqual(report.drive.model, "Ultra USB 3.0")
        self.assertEqual(report.drive.vendor, "SanDisk")
        self.assertEqual(report.drive.serial, "0401568289410948")
        self.assertEqual(report.drive.size_bytes, 32010928128)
        self.assertTrue(report.drive.is_removable)
        self.assertFalse(report.drive.is_read_only)

        # Verify Partition Layout
        self.assertEqual(len(report.partitions), 1)
        part = report.partitions[0]
        self.assertEqual(part.name, "sdb1")
        self.assertEqual(part.path, "/dev/sdb1")
        self.assertEqual(part.partition_scheme, "dos")
        self.assertEqual(part.partition_type, "0xc")
        self.assertEqual(part.filesystem, "vfat")
        self.assertEqual(part.label, "DJ_SET_1")
        self.assertEqual(part.uuid, "7B42-19E4")
        self.assertEqual(part.mount_points, ("/media/user/DJ_SET_1",))
        self.assertEqual(part.size_bytes, 32010928128)
        self.assertFalse(part.is_read_only)

        # Verify Free Space Correlation
        space = report.space_for_mount("/media/user/DJ_SET_1")
        self.assertIsNotNone(space)
        self.assertEqual(
            space,
            SpaceInfo(
                total_bytes=32010928128,
                used_bytes=1468940288,
                available_bytes=30541987840,
            ),
        )

        # Verify command executions strictly match fixed arrays
        self.assertEqual(executed, [list(LSBLK_COMMAND), list(DF_COMMAND)])

    def test_inspect_system_disk(self) -> None:
        """Inspector correctly records non-removable system disk with multi-partition layout."""
        _, inspector = _make_mock_runner(
            lsblk_stdout=SYSTEM_DISK_LSBLK_JSON,
            df_stdout=SYSTEM_DISK_DF_OUTPUT,
        )

        inspections = inspector.inspect_all()
        self.assertEqual(len(inspections), 1)
        report = inspections[0]

        self.assertEqual(report.drive.name, "nvme0n1")
        self.assertFalse(report.drive.is_removable)
        self.assertEqual(report.drive.vendor, "Samsung")
        self.assertEqual(report.drive.model, "Samsung SSD 980 PRO 1TB")
        self.assertEqual(report.drive.size_bytes, 1000204886016)

        self.assertEqual(len(report.partitions), 3)

        p1 = report.partitions[0]
        self.assertEqual(p1.name, "nvme0n1p1")
        self.assertEqual(p1.partition_scheme, "gpt")
        self.assertEqual(p1.filesystem, "vfat")
        self.assertEqual(p1.label, "SYSTEM_BOOT")
        self.assertEqual(p1.mount_points, ("/boot/efi",))

        p2 = report.partitions[1]
        self.assertEqual(p2.name, "nvme0n1p2")
        self.assertEqual(p2.filesystem, "ext4")
        self.assertEqual(p2.label, "ROOT_FS")
        self.assertEqual(p2.mount_points, ("/",))

        p3 = report.partitions[2]
        self.assertEqual(p3.name, "nvme0n1p3")
        self.assertEqual(p3.filesystem, "swap")
        self.assertEqual(p3.mount_points, ())

        self.assertEqual(
            report.space_for_mount("/boot/efi"),
            SpaceInfo(
                total_bytes=629145600,
                used_bytes=104857600,
                available_bytes=524288000,
            ),
        )
        self.assertEqual(
            report.space_for_mount("/"),
            SpaceInfo(
                total_bytes=950000000000,
                used_bytes=500000000000,
                available_bytes=420000000000,
            ),
        )
        self.assertIsNone(report.space_for_mount("/nonexistent"))

    def test_inspect_specific_device_by_name_and_path(self) -> None:
        """Targeted device inspection locates devices by base name or /dev path."""
        _, inspector = _make_mock_runner(
            lsblk_stdout=FAT32_STICK_LSBLK_JSON,
            df_stdout=FAT32_STICK_DF_OUTPUT,
        )

        by_name = inspector.inspect_device("sdb")
        self.assertEqual(by_name.drive.name, "sdb")

        by_path = inspector.inspect_device("/dev/sdb")
        self.assertEqual(by_path.drive.path, "/dev/sdb")

        with self.assertRaises(CommandExecutionError):
            inspector.inspect_device("sdz")

    def test_malformed_lsblk_json_raises_parse_error(self) -> None:
        """Syntax errors in lsblk output raise InspectionParseError."""
        _, inspector = _make_mock_runner(lsblk_stdout='{"blockdevices": [')
        with self.assertRaises(InspectionParseError):
            inspector.inspect_all()

    def test_missing_blockdevices_key_raises_parse_error(self) -> None:
        """Missing 'blockdevices' list in lsblk JSON raises InspectionParseError."""
        _, inspector = _make_mock_runner(lsblk_stdout='{"unrelated": []}')
        with self.assertRaises(InspectionParseError):
            inspector.inspect_all()

    def test_lsblk_command_failure_raises_execution_error(self) -> None:
        """Non-zero exit code from lsblk raises CommandExecutionError."""
        _, inspector = _make_mock_runner(lsblk_returncode=1)
        with self.assertRaises(CommandExecutionError):
            inspector.inspect_all()

    def test_df_command_failure_raises_execution_error(self) -> None:
        """Non-zero exit code from df raises CommandExecutionError."""
        _, inspector = _make_mock_runner(
            lsblk_stdout=FAT32_STICK_LSBLK_JSON,
            df_returncode=1,
        )
        with self.assertRaises(CommandExecutionError):
            inspector.inspect_all()

    def test_whole_disk_unpartitioned_filesystem(self) -> None:
        """A disk formatted without a partition table reports the filesystem on the disk."""
        whole_disk_json = json.dumps(
            {
                "blockdevices": [
                    {
                        "name": "sdc",
                        "path": "/dev/sdc",
                        "model": "Floppy or Superfloppy",
                        "vendor": "Generic",
                        "serial": "11223344",
                        "rm": True,
                        "ro": False,
                        "type": "disk",
                        "fstype": "vfat",
                        "label": "NO_PART_TABLE",
                        "uuid": "4321-DCBA",
                        "fsavail": 1000000,
                        "fsused": 200000,
                        "fssize": 1200000,
                        "mountpoints": ["/media/user/NO_PART_TABLE"],
                        "pttype": None,
                        "parttype": None,
                        "size": 1200000,
                        "children": None,
                    }
                ]
            }
        )
        _, inspector = _make_mock_runner(
            lsblk_stdout=whole_disk_json,
            df_stdout="",
        )

        inspections = inspector.inspect_all()
        self.assertEqual(len(inspections), 1)
        report = inspections[0]
        self.assertEqual(len(report.partitions), 1)
        part = report.partitions[0]
        self.assertEqual(part.filesystem, "vfat")
        self.assertEqual(part.label, "NO_PART_TABLE")
        self.assertIsNone(part.partition_scheme)
        # Falls back to lsblk space when df does not have it
        space = report.space_for_mount("/media/user/NO_PART_TABLE")
        self.assertEqual(
            space,
            SpaceInfo(total_bytes=1200000, used_bytes=200000, available_bytes=1000000),
        )

    def test_inspect_exfat_stick(self) -> None:
        """Inspector correctly detects exFAT filesystem and records drive metrics."""
        _, inspector = _make_mock_runner(
            lsblk_stdout=EXFAT_STICK_LSBLK_JSON,
            df_stdout=EXFAT_STICK_DF_OUTPUT,
        )

        inspections = inspector.inspect_all()
        self.assertEqual(len(inspections), 1)
        report = inspections[0]

        self.assertTrue(report.drive.is_removable)
        self.assertEqual(report.drive.name, "sdc")
        self.assertEqual(report.drive.model, "Extreme PRO")
        self.assertTrue(report.has_exfat_partition)
        self.assertFalse(report.has_fat32_partition)

        self.assertEqual(len(report.partitions), 1)
        part = report.partitions[0]
        self.assertEqual(part.name, "sdc1")
        self.assertEqual(part.filesystem, "exfat")
        self.assertTrue(part.is_exfat)
        self.assertFalse(part.is_fat32)
        self.assertEqual(part.label, "EXFAT_MUSIC")
        self.assertEqual(part.partition_scheme, "dos")
        self.assertEqual(part.partition_type, "0x7")

        space = report.space_for_mount("/media/user/EXFAT_MUSIC")
        self.assertEqual(
            space,
            SpaceInfo(
                total_bytes=128035676160,
                used_bytes=8035676160,
                available_bytes=120000000000,
            ),
        )

    def test_inspect_exfat_gpt_ssd(self) -> None:
        """Inspector correctly parses an exFAT partition on a GPT partition table."""
        _, inspector = _make_mock_runner(
            lsblk_stdout=EXFAT_GPT_SSD_LSBLK_JSON,
            df_stdout=EXFAT_GPT_SSD_DF_OUTPUT,
        )

        report = inspector.inspect_device("sdd")
        self.assertTrue(report.drive.is_removable)
        self.assertEqual(report.drive.model, "T7 Shield")
        self.assertTrue(report.has_exfat_partition)

        part = report.partitions[0]
        self.assertEqual(part.partition_scheme, "gpt")
        self.assertEqual(part.partition_type, "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7")
        self.assertEqual(part.filesystem, "exfat")
        self.assertTrue(part.is_exfat)
        self.assertEqual(part.label, "T7_DJ_ARCHIVE")

        space = report.space_for_mount("/media/user/T7_DJ_ARCHIVE")
        self.assertEqual(
            space,
            SpaceInfo(
                total_bytes=2000398934016,
                used_bytes=500398934016,
                available_bytes=1500000000000,
            ),
        )


if __name__ == "__main__":
    unittest.main()
