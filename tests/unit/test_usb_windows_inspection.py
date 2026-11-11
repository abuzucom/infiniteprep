"""Unit tests for read-only Windows USB drive and filesystem inspection (D93, D161, D181, Rule 1).

Fixtures mirror recorded ConvertTo-Json output from the fixed Get-Disk,
Get-Partition, and Get-Volume command projections. Identifiers are synthetic
placeholders, never real local identifiers.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from typing import Final

from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveInspection,
    InspectionParseError,
    SpaceInfo,
)
from infiniteprep.services.usb.windows import (
    DISK_COMMAND,
    DISK_COMMAND_TEXT,
    MAX_COMMAND_OUTPUT_BYTES,
    MAX_ERROR_TEXT_LEN,
    PARTITION_COMMAND,
    PARTITION_COMMAND_TEXT,
    VOLUME_COMMAND,
    VOLUME_COMMAND_TEXT,
    WindowsDriveInspector,
    _decode_capped,
)

_BS: Final[str] = chr(92)
_FAT32_GUID: Final[str] = "11111111-1111-1111-1111-111111111111"
_EFI_GUID: Final[str] = "22222222-2222-2222-2222-222222222222"
_RECOVERY_GUID: Final[str] = "33333333-3333-3333-3333-333333333333"
_BASIC_GUID: Final[str] = "44444444-4444-4444-4444-444444444444"
_EXFAT_GUID: Final[str] = "55555555-5555-5555-5555-555555555555"
_EFI_TYPE: Final[str] = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
_MSR_TYPE: Final[str] = "e3c9e316-0b5c-4db8-817d-f92df00215ae"
_DATA_TYPE: Final[str] = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
_RECOVERY_TYPE: Final[str] = "de94bba4-06d1-4d40-a16a-bfd50179d6ac"


def _volume_access_path(guid: str) -> str:
    """Build a \\?\\Volume{guid}\\ access path without literal backslash runs."""
    return _BS + _BS + "?" + _BS + "Volume{" + guid + "}" + _BS


def _physical_drive_path(number: int) -> str:
    """Build a \\.\\PHYSICALDRIVE{number} device path without literal backslash runs."""
    return _BS + _BS + "." + _BS + "PHYSICALDRIVE" + str(number)


def _mount(letter: str) -> str:
    """Build a drive-letter mount point such as E:\\."""
    return letter + ":" + _BS


FAT32_STICK_DISKS_JSON: Final[str] = json.dumps(
    [
        {
            "DiskNumber": 1,
            "Model": "TestDrive FAT32 Stick",
            "Manufacturer": "TestVendor",
            "SerialNumber": "TESTSERIAL0001",
            "BusType": "USB",
            "Size": 32010928128,
            "PartitionStyle": "MBR",
            "IsReadOnly": False,
        }
    ]
)
FAT32_STICK_PARTITIONS_JSON: Final[str] = json.dumps(
    [
        {
            "DiskNumber": 1,
            "PartitionNumber": 1,
            "Size": 32009879552,
            "Type": "Basic",
            "GptType": None,
            "DriveLetter": "E",
            "IsReadOnly": False,
            "AccessPaths": [_mount("E"), _volume_access_path(_FAT32_GUID)],
        }
    ]
)
FAT32_STICK_VOLUMES_JSON: Final[str] = json.dumps(
    [
        {
            "DriveLetter": "E",
            "FileSystemLabel": "DJ_SET_1",
            "FileSystem": "FAT32",
            "Size": 32009879552,
            "SizeRemaining": 30541987840,
            "DriveType": "Removable",
        }
    ]
)

SYSTEM_DISK_DISKS_JSON: Final[str] = json.dumps(
    [
        {
            "DiskNumber": 0,
            "Model": "TestDrive System SSD",
            "Manufacturer": None,
            "SerialNumber": "TESTSERIAL0000",
            "BusType": "NVMe",
            "Size": 1024209543168,
            "PartitionStyle": "GPT",
            "IsReadOnly": False,
        }
    ]
)
SYSTEM_DISK_PARTITIONS_JSON: Final[str] = json.dumps(
    [
        {
            "DiskNumber": 0,
            "PartitionNumber": 1,
            "Size": 104857600,
            "Type": "System",
            "GptType": "{" + _EFI_TYPE + "}",
            "DriveLetter": None,
            "IsReadOnly": None,
            "AccessPaths": [_volume_access_path(_EFI_GUID)],
        },
        {
            "DiskNumber": 0,
            "PartitionNumber": 2,
            "Size": 16759808,
            "Type": "Reserved",
            "GptType": "{" + _MSR_TYPE + "}",
            "DriveLetter": None,
            "IsReadOnly": None,
            "AccessPaths": None,
        },
        {
            "DiskNumber": 0,
            "PartitionNumber": 3,
            "Size": 1022949687296,
            "Type": "Basic",
            "GptType": "{" + _DATA_TYPE + "}",
            "DriveLetter": "C",
            "IsReadOnly": False,
            "AccessPaths": [_mount("C"), _volume_access_path(_BASIC_GUID)],
        },
        {
            "DiskNumber": 0,
            "PartitionNumber": 4,
            "Size": 1151336448,
            "Type": "Recovery",
            "GptType": "{" + _RECOVERY_TYPE + "}",
            "DriveLetter": None,
            "IsReadOnly": None,
            "AccessPaths": [_volume_access_path(_RECOVERY_GUID)],
        },
    ]
)
SYSTEM_DISK_VOLUMES_JSON: Final[str] = json.dumps(
    [
        {
            "DriveLetter": "C",
            "FileSystemLabel": "Windows",
            "FileSystem": "NTFS",
            "Size": 1022949683200,
            "SizeRemaining": 163168677888,
            "DriveType": "Fixed",
        }
    ]
)

EXFAT_STICK_DISKS_JSON: Final[str] = json.dumps(
    [
        {
            "DiskNumber": 5,
            "Model": "  TestDrive Exfat Stick  ",
            "Manufacturer": " USB",
            "SerialNumber": "TESTSERIAL0005",
            "BusType": "USB",
            "Size": 123048296448,
            "PartitionStyle": "GPT",
            "IsReadOnly": False,
        }
    ]
)
EXFAT_STICK_PARTITIONS_JSON: Final[str] = json.dumps(
    [
        {
            "DiskNumber": 5,
            "PartitionNumber": 1,
            "Size": 123046199296,
            "Type": "Basic",
            "GptType": "{" + _DATA_TYPE + "}",
            "DriveLetter": "I",
            "IsReadOnly": None,
            "AccessPaths": [_mount("I"), _volume_access_path(_EXFAT_GUID)],
        }
    ]
)
EXFAT_STICK_VOLUMES_JSON: Final[str] = json.dumps(
    [
        {
            "DriveLetter": "I",
            "FileSystemLabel": "",
            "FileSystem": "exFAT",
            "Size": 123040956416,
            "SizeRemaining": 102923239424,
            "DriveType": "Removable",
        }
    ]
)


def _make_mock_runner(
    disks_stdout: str = "[]",
    partitions_stdout: str = "[]",
    volumes_stdout: str = "[]",
    *,
    disks_returncode: int = 0,
    partitions_returncode: int = 0,
    volumes_returncode: int = 0,
) -> tuple[list[list[str]], WindowsDriveInspector]:
    """Create an inspector with an intercepted command execution list."""
    executed_commands: list[list[str]] = []

    def mock_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        executed_commands.append(list(cmd))
        if cmd == list(DISK_COMMAND):
            return subprocess.CompletedProcess(
                cmd,
                returncode=disks_returncode,
                stdout=disks_stdout,
                stderr="Get-Disk mock error" if disks_returncode != 0 else "",
            )
        if cmd == list(PARTITION_COMMAND):
            return subprocess.CompletedProcess(
                cmd,
                returncode=partitions_returncode,
                stdout=partitions_stdout,
                stderr="Get-Partition mock error" if partitions_returncode != 0 else "",
            )
        if cmd == list(VOLUME_COMMAND):
            return subprocess.CompletedProcess(
                cmd,
                returncode=volumes_returncode,
                stdout=volumes_stdout,
                stderr="Get-Volume mock error" if volumes_returncode != 0 else "",
            )
        raise AssertionError(f"Unexpected command executed: {cmd}")

    inspector = WindowsDriveInspector(command_runner=mock_runner)
    return executed_commands, inspector


class TestUsbWindowsInspection(unittest.TestCase):
    """Test suite for Windows drive inspection parsing, free space correlation, and errors."""

    def test_inspect_fat32_stick(self) -> None:
        """Inspector extracts identity, partition, filesystem, and space for a FAT32 stick."""
        _, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON,
            partitions_stdout=FAT32_STICK_PARTITIONS_JSON,
            volumes_stdout=FAT32_STICK_VOLUMES_JSON,
        )

        inspections = inspector.inspect_all()
        self.assertEqual(len(inspections), 1)
        report: DriveInspection = inspections[0]

        self.assertEqual(report.drive.name, "disk1")
        self.assertEqual(report.drive.path, _physical_drive_path(1))
        self.assertEqual(report.drive.model, "TestDrive FAT32 Stick")
        self.assertEqual(report.drive.vendor, "TestVendor")
        self.assertEqual(report.drive.serial, "TESTSERIAL0001")
        self.assertEqual(report.drive.size_bytes, 32010928128)
        self.assertTrue(report.drive.is_removable)
        self.assertFalse(report.drive.is_read_only)
        self.assertEqual(report.drive.bus_type, "USB")

        self.assertEqual(len(report.partitions), 1)
        partition = report.partitions[0]
        self.assertEqual(partition.name, "disk1part1")
        self.assertEqual(partition.partition_number, 1)
        self.assertEqual(partition.partition_scheme, "mbr")
        self.assertEqual(partition.partition_type, "Basic")
        self.assertEqual(partition.filesystem, "FAT32")
        self.assertEqual(partition.label, "DJ_SET_1")
        self.assertEqual(partition.uuid, _FAT32_GUID)
        self.assertEqual(partition.mount_points, (_mount("E"),))
        self.assertEqual(partition.size_bytes, 32009879552)
        self.assertEqual(partition.path, _BS + _BS + "." + _BS + "Volume{" + _FAT32_GUID + "}")
        self.assertTrue(report.has_fat32_partition)
        self.assertFalse(report.has_exfat_partition)

        space = report.space_for_mount(_mount("E"))
        self.assertIsNotNone(space)
        self.assertEqual(space.total_bytes, 32009879552)
        self.assertEqual(space.available_bytes, 30541987840)
        self.assertEqual(space.used_bytes, 32009879552 - 30541987840)

    def test_inspect_exfat_stick(self) -> None:
        """Inspector detects exFAT and strips padded model and vendor metadata."""
        _, inspector = _make_mock_runner(
            disks_stdout=EXFAT_STICK_DISKS_JSON,
            partitions_stdout=EXFAT_STICK_PARTITIONS_JSON,
            volumes_stdout=EXFAT_STICK_VOLUMES_JSON,
        )

        report = inspector.inspect_all()[0]
        self.assertEqual(report.drive.name, "disk5")
        self.assertEqual(report.drive.model, "TestDrive Exfat Stick")
        self.assertEqual(report.drive.vendor, "USB")
        self.assertTrue(report.drive.is_removable)
        self.assertTrue(report.has_exfat_partition)
        self.assertFalse(report.has_fat32_partition)
        self.assertEqual(report.partitions[0].filesystem, "exFAT")
        self.assertIsNone(report.partitions[0].label)
        self.assertEqual(report.partitions[0].partition_scheme, "gpt")

    def test_inspect_system_disk(self) -> None:
        """Inspector records EFI, MSR, data, and recovery partitions of a GPT system disk."""
        _, inspector = _make_mock_runner(
            disks_stdout=SYSTEM_DISK_DISKS_JSON,
            partitions_stdout=SYSTEM_DISK_PARTITIONS_JSON,
            volumes_stdout=SYSTEM_DISK_VOLUMES_JSON,
        )

        report = inspector.inspect_all()[0]
        self.assertEqual(report.drive.name, "disk0")
        self.assertFalse(report.drive.is_removable)
        self.assertIsNone(report.drive.vendor)
        self.assertEqual(len(report.partitions), 4)

        by_number = {part.partition_number: part for part in report.partitions}
        efi = by_number[1]
        self.assertEqual(efi.partition_type, _EFI_TYPE)
        self.assertEqual(efi.partition_scheme, "gpt")
        self.assertEqual(efi.mount_points, ())
        self.assertIsNone(efi.filesystem)
        self.assertEqual(efi.uuid, _EFI_GUID)
        self.assertEqual(efi.path, _BS + _BS + "." + _BS + "Volume{" + _EFI_GUID + "}")

        msr = by_number[2]
        self.assertEqual(msr.partition_type, _MSR_TYPE)
        self.assertIsNone(msr.uuid)
        self.assertEqual(msr.path, "disk0part2")

        data = by_number[3]
        self.assertEqual(data.filesystem, "NTFS")
        self.assertEqual(data.label, "Windows")
        self.assertEqual(data.mount_points, (_mount("C"),))

        recovery = by_number[4]
        self.assertEqual(recovery.partition_type, _RECOVERY_TYPE)
        self.assertEqual(recovery.uuid, _RECOVERY_GUID)

        space = report.space_for_mount(_mount("C"))
        self.assertIsNotNone(space)
        self.assertEqual(space.total_bytes, 1022949683200)
        self.assertEqual(space.available_bytes, 163168677888)

    def test_inspect_unpartitioned_disk(self) -> None:
        """A disk with no partition entries yields an empty partition tuple."""
        _, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON,
            partitions_stdout="[]",
            volumes_stdout="[]",
        )

        report = inspector.inspect_all()[0]
        self.assertEqual(report.partitions, ())
        self.assertEqual(report.space_by_mount, ())

    def test_inspect_device_by_name_number_and_path(self) -> None:
        """inspect_device accepts disk names, numbers, and PHYSICALDRIVE paths."""
        _, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON,
            partitions_stdout=FAT32_STICK_PARTITIONS_JSON,
            volumes_stdout=FAT32_STICK_VOLUMES_JSON,
        )

        by_name = inspector.inspect_device("disk1")
        by_number = inspector.inspect_device("1")
        by_path = inspector.inspect_device(_physical_drive_path(1))
        self.assertEqual(by_name.drive.name, "disk1")
        self.assertEqual(by_number.drive.name, "disk1")
        self.assertEqual(by_path.drive.name, "disk1")

    def test_inspect_device_rejects_invalid_targets(self) -> None:
        """inspect_device rejects traversal, letters, and malformed targets."""
        _, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON,
            partitions_stdout=FAT32_STICK_PARTITIONS_JSON,
            volumes_stdout=FAT32_STICK_VOLUMES_JSON,
        )

        for bad_target in ("../../disk1", "disk", "diskX", _mount("E"), "", "disk 1"):
            with self.subTest(target=bad_target), self.assertRaises(CommandExecutionError):
                inspector.inspect_device(bad_target)

    def test_inspect_device_not_found(self) -> None:
        """inspect_device raises CommandExecutionError for an absent disk number."""
        _, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON,
            partitions_stdout=FAT32_STICK_PARTITIONS_JSON,
            volumes_stdout=FAT32_STICK_VOLUMES_JSON,
        )
        with self.assertRaises(CommandExecutionError):
            inspector.inspect_device("9")

    def test_fixed_commands_executed_in_order(self) -> None:
        """The inspector runs exactly the three fixed command arrays, in order."""
        executed, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON,
            partitions_stdout=FAT32_STICK_PARTITIONS_JSON,
            volumes_stdout=FAT32_STICK_VOLUMES_JSON,
        )
        inspector.inspect_all()
        self.assertEqual(
            executed,
            [list(DISK_COMMAND), list(PARTITION_COMMAND), list(VOLUME_COMMAND)],
        )

    def test_commands_pin_system_powershell(self) -> None:
        """Command arrays target the SystemRoot-pinned interpreter as fixed arrays."""
        system_root = os.environ.get("SYSTEMROOT") or "C:\\Windows"
        expected_exe = system_root + r"\System32\WindowsPowerShell\v1.0\powershell.exe"
        for command, text in (
            (DISK_COMMAND, DISK_COMMAND_TEXT),
            (PARTITION_COMMAND, PARTITION_COMMAND_TEXT),
            (VOLUME_COMMAND, VOLUME_COMMAND_TEXT),
        ):
            self.assertEqual(
                command,
                (expected_exe, "-NoProfile", "-NonInteractive", "-Command", text),
            )

    def test_oversized_output_raises_parse_error(self) -> None:
        """Output beyond the byte limit raises InspectionParseError before parsing."""
        _, inspector = _make_mock_runner(disks_stdout="[" + "a" * (MAX_COMMAND_OUTPUT_BYTES) + "]")
        with self.assertRaises(InspectionParseError):
            inspector.inspect_all()

    def test_decode_capped_rejects_oversized_output(self) -> None:
        """The byte cap rejects oversized stdout and stderr before decoding."""
        oversized = b"a" * (MAX_COMMAND_OUTPUT_BYTES + 1)
        with self.assertRaises(InspectionParseError):
            _decode_capped(oversized, b"")
        with self.assertRaises(InspectionParseError):
            _decode_capped(b"", oversized)
        self.assertEqual(
            _decode_capped(b"\xff\xfebad", b"ok"),
            ("\ufffd\ufffdbad", "ok"),
        )

    def test_command_failure_message_is_bounded(self) -> None:
        """A huge stderr reply yields a bounded error message."""

        def loud_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "E" * (MAX_ERROR_TEXT_LEN * 4))

        noisy = WindowsDriveInspector(command_runner=loud_runner)
        with self.assertRaises(CommandExecutionError) as ctx:
            noisy.inspect_all()
        self.assertLessEqual(len(str(ctx.exception)), MAX_ERROR_TEXT_LEN)

    def test_malformed_json_raises_parse_error(self) -> None:
        """Malformed JSON from any command raises InspectionParseError."""
        for bad in ("not json{", "null and a list", "[1, 2]"):
            for position in range(3):
                kwargs = {
                    "disks_stdout": "[]" if position != 0 else bad,
                    "partitions_stdout": "[]" if position != 1 else bad,
                    "volumes_stdout": "[]" if position != 2 else bad,
                }
                _, inspector = _make_mock_runner(**kwargs)
                with (
                    self.subTest(payload=bad, position=position),
                    self.assertRaises(InspectionParseError),
                ):
                    inspector.inspect_all()

    def test_single_object_output_wrapped(self) -> None:
        """A single JSON object (no array) is treated as one entry."""
        single_disk = json.dumps(
            {
                "DiskNumber": 2,
                "Model": "TestDrive Single",
                "Manufacturer": None,
                "SerialNumber": "TESTSERIAL0002",
                "BusType": "SATA",
                "Size": 1000,
                "PartitionStyle": "GPT",
                "IsReadOnly": False,
            }
        )
        _, inspector = _make_mock_runner(disks_stdout=single_disk)
        inspections = inspector.inspect_all()
        self.assertEqual(len(inspections), 1)
        self.assertEqual(inspections[0].drive.name, "disk2")

    def test_empty_stdout_returns_no_disks(self) -> None:
        """Empty command output (no disks) yields an empty inspection tuple."""
        _, inspector = _make_mock_runner(disks_stdout="")
        self.assertEqual(inspector.inspect_all(), ())

    def test_missing_disk_number_raises_parse_error(self) -> None:
        """A disk entry without a valid DiskNumber raises InspectionParseError."""
        bad_entries = ("{}", '{"DiskNumber": "abc"}', '{"DiskNumber": -1}', '{"DiskNumber": null}')
        for bad_entry in bad_entries:
            _, inspector = _make_mock_runner(disks_stdout="[" + bad_entry + "]")
            with self.subTest(entry=bad_entry), self.assertRaises(InspectionParseError):
                inspector.inspect_all()

    def test_invalid_partition_number_raises_parse_error(self) -> None:
        """Partition entries without a positive PartitionNumber raise InspectionParseError."""
        for bad_entry in ('{"DiskNumber": 1}', '{"DiskNumber": 1, "PartitionNumber": 0}'):
            _, inspector = _make_mock_runner(partitions_stdout="[" + bad_entry + "]")
            with self.subTest(entry=bad_entry), self.assertRaises(InspectionParseError):
                inspector.inspect_all()

    def test_command_failure_raises_execution_error(self) -> None:
        """A nonzero exit from any command raises CommandExecutionError."""
        for key in ("disks_returncode", "partitions_returncode", "volumes_returncode"):
            kwargs = {
                key: 1,
                "disks_stdout": "[]",
                "partitions_stdout": "[]",
                "volumes_stdout": "[]",
            }
            _, inspector = _make_mock_runner(**kwargs)
            with self.subTest(command=key), self.assertRaises(CommandExecutionError):
                inspector.inspect_all()

    def test_command_timeout_wrapped_as_execution_error(self) -> None:
        """A runner timeout surfaces as CommandExecutionError."""

        def timeout_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd, 15)

        inspector = WindowsDriveInspector(command_runner=timeout_runner)
        with self.assertRaises(CommandExecutionError):
            inspector.inspect_all()

    def test_untrusted_metadata_sanitized(self) -> None:
        """ANSI escapes and control characters are stripped from untrusted metadata."""
        evil_model = "Evil" + chr(27) + "[31mModel" + chr(0) + "Name"
        evil_label = "Bad" + chr(27) + "cLabel" + chr(7)
        long_label = "L" * 600
        disks = json.dumps(
            [
                {
                    "DiskNumber": 1,
                    "Model": evil_model,
                    "Manufacturer": None,
                    "SerialNumber": None,
                    "BusType": "USB",
                    "Size": 1000,
                    "PartitionStyle": "MBR",
                    "IsReadOnly": False,
                }
            ]
        )
        partitions = json.dumps(
            [
                {
                    "DiskNumber": 1,
                    "PartitionNumber": 1,
                    "Size": 900,
                    "Type": "Basic",
                    "GptType": None,
                    "DriveLetter": "E",
                    "IsReadOnly": False,
                    "AccessPaths": None,
                }
            ]
        )
        volumes = json.dumps(
            [
                {
                    "DriveLetter": "E",
                    "FileSystemLabel": evil_label + long_label,
                    "FileSystem": "FAT32",
                    "Size": 900,
                    "SizeRemaining": 100,
                    "DriveType": "Removable",
                }
            ]
        )
        _, inspector = _make_mock_runner(
            disks_stdout=disks, partitions_stdout=partitions, volumes_stdout=volumes
        )
        report = inspector.inspect_all()[0]
        self.assertEqual(report.drive.model, "EvilModelName")
        self.assertEqual(len(report.partitions[0].label or ""), 512)
        self.assertNotIn(chr(27), report.partitions[0].label or "")

    def test_malformed_drive_letters_filtered(self) -> None:
        """Drive letters that are not single letters produce no mount point."""
        partitions = json.dumps(
            [
                {
                    "DiskNumber": 1,
                    "PartitionNumber": 1,
                    "Size": 900,
                    "Type": "Basic",
                    "GptType": None,
                    "DriveLetter": "E:",
                    "IsReadOnly": False,
                    "AccessPaths": None,
                },
                {
                    "DiskNumber": 1,
                    "PartitionNumber": 2,
                    "Size": 800,
                    "Type": "Basic",
                    "GptType": None,
                    "DriveLetter": "7",
                    "IsReadOnly": False,
                    "AccessPaths": None,
                },
            ]
        )
        _, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON, partitions_stdout=partitions
        )
        report = inspector.inspect_all()[0]
        self.assertEqual(report.partitions[0].mount_points, ())
        self.assertEqual(report.partitions[1].mount_points, ())
        self.assertEqual(report.space_by_mount, ())

    def test_malformed_access_paths_ignored(self) -> None:
        """AccessPaths entries that are not volume GUID paths are ignored."""
        partitions = json.dumps(
            [
                {
                    "DiskNumber": 1,
                    "PartitionNumber": 1,
                    "Size": 900,
                    "Type": "Basic",
                    "GptType": None,
                    "DriveLetter": "E",
                    "IsReadOnly": False,
                    "AccessPaths": [
                        "../../etc/passwd",
                        "relative/path",
                        "not-a-volume-path",
                        _volume_access_path(_FAT32_GUID),
                    ],
                }
            ]
        )
        _, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON,
            partitions_stdout=partitions,
            volumes_stdout=FAT32_STICK_VOLUMES_JSON,
        )
        report = inspector.inspect_all()[0]
        self.assertEqual(report.partitions[0].uuid, _FAT32_GUID)
        self.assertEqual(
            report.partitions[0].path, _BS + _BS + "." + _BS + "Volume{" + _FAT32_GUID + "}"
        )

    def test_removable_inferred_from_volume_drive_type(self) -> None:
        """A non-USB disk with a Removable volume is reported as removable."""
        disks = json.dumps(
            [
                {
                    "DiskNumber": 3,
                    "Model": "TestDrive SATA",
                    "Manufacturer": None,
                    "SerialNumber": None,
                    "BusType": "SATA",
                    "Size": 1000,
                    "PartitionStyle": "GPT",
                    "IsReadOnly": False,
                }
            ]
        )
        partitions = json.dumps(
            [
                {
                    "DiskNumber": 3,
                    "PartitionNumber": 1,
                    "Size": 900,
                    "Type": "Basic",
                    "GptType": None,
                    "DriveLetter": "F",
                    "IsReadOnly": False,
                    "AccessPaths": None,
                }
            ]
        )
        volumes = json.dumps(
            [
                {
                    "DriveLetter": "F",
                    "FileSystemLabel": None,
                    "FileSystem": "exFAT",
                    "Size": 900,
                    "SizeRemaining": 100,
                    "DriveType": "Removable",
                }
            ]
        )
        _, inspector = _make_mock_runner(
            disks_stdout=disks, partitions_stdout=partitions, volumes_stdout=volumes
        )
        report = inspector.inspect_all()[0]
        self.assertTrue(report.drive.is_removable)

    def test_size_used_fallback(self) -> None:
        """Volumes exposing SizeUsed instead of SizeRemaining still yield space data."""
        volumes = json.dumps(
            [
                {
                    "DriveLetter": "E",
                    "FileSystemLabel": None,
                    "FileSystem": "FAT32",
                    "Size": 100,
                    "SizeUsed": 30,
                    "DriveType": "Removable",
                }
            ]
        )
        _, inspector = _make_mock_runner(
            disks_stdout=FAT32_STICK_DISKS_JSON,
            partitions_stdout=FAT32_STICK_PARTITIONS_JSON,
            volumes_stdout=volumes,
        )
        report = inspector.inspect_all()[0]
        space = report.space_for_mount(_mount("E"))
        self.assertIsInstance(space, SpaceInfo)
        self.assertEqual(space.total_bytes, 100)
        self.assertEqual(space.used_bytes, 30)
        self.assertEqual(space.available_bytes, 70)

    def test_partition_ordering_is_stable(self) -> None:
        """Partitions are reported ordered by partition number regardless of input order."""
        disks = json.dumps(
            [
                {
                    "DiskNumber": 4,
                    "Model": "TestDrive",
                    "Manufacturer": None,
                    "SerialNumber": None,
                    "BusType": "USB",
                    "Size": 1000,
                    "PartitionStyle": "GPT",
                    "IsReadOnly": False,
                }
            ]
        )
        partitions = json.dumps(
            [
                {
                    "DiskNumber": 4,
                    "PartitionNumber": 2,
                    "Size": 500,
                    "Type": "Basic",
                    "GptType": None,
                    "DriveLetter": None,
                    "IsReadOnly": False,
                    "AccessPaths": None,
                },
                {
                    "DiskNumber": 4,
                    "PartitionNumber": 1,
                    "Size": 400,
                    "Type": "Basic",
                    "GptType": None,
                    "DriveLetter": None,
                    "IsReadOnly": False,
                    "AccessPaths": None,
                },
            ]
        )
        _, inspector = _make_mock_runner(disks_stdout=disks, partitions_stdout=partitions)
        report = inspector.inspect_all()[0]
        self.assertEqual([part.partition_number for part in report.partitions], [1, 2])


if __name__ == "__main__":
    unittest.main()
