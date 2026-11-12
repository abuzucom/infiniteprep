"""Unit tests for the per-OS USB preflight probes (D93, D131, D138, D186, D187)."""

from __future__ import annotations

import json
import subprocess
import unittest
from typing import Any

from infiniteprep.services.usb.linux_probe import (
    LSBLK_FACTS_COMMAND,
    UDEVADM_COMMAND_PREFIX,
    LinuxPreflightProbe,
)
from infiniteprep.services.usb.macos_probe import MacOSPreflightProbe
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveInspection,
    FilesystemHealth,
    InspectionParseError,
)
from infiniteprep.services.usb.preflight import (
    FACT_ACTIVE_PARTITION,
    FACT_FILESYSTEM_HEALTH,
    FACT_PRIMARY_PARTITION,
    RefusalCode,
    evaluate_preflight,
)
from infiniteprep.services.usb.probe_facts import (
    DriveFacts,
    PartitionFacts,
    apply_drive_facts,
    classify_mbr_partition,
)
from infiniteprep.services.usb.windows import POWERSHELL_EXE
from infiniteprep.services.usb.windows_probe import (
    DISK_FACTS_COMMAND,
    PARTITION_FACTS_COMMAND,
    VOLUME_HEALTH_COMMAND,
    WindowsPreflightProbe,
)
from tests.unit.usb_preflight_support import (
    MOUNT_PATH,
    build_drive,
    build_inspection,
    build_partition,
    build_request,
)

UDEV_PROPERTIES = "DEVNAME=/dev/sdb\nID_VENDOR_ID=0781\nID_MODEL_ID=5581\nID_BUS=usb\n"
WRITABLE_MOUNT_TABLE = f"/dev/sdb1 {MOUNT_PATH} vfat rw,relatime,errors=remount-ro 0 0\n"
READ_ONLY_MOUNT_TABLE = f"/dev/sdb1 {MOUNT_PATH} vfat ro,relatime,errors=remount-ro 0 0\n"
WINDOWS_MOUNT = "E:\\"


def build_unprobed_inspection(**partition_changes: Any) -> DriveInspection:
    """Return the passing stick with every probe fact absent."""
    partition = build_partition(
        is_active=None, is_primary=None, filesystem_health=None, **partition_changes
    )
    return build_inspection(
        drive=build_drive(hardware_id=None, is_system=None), partitions=(partition,)
    )


def build_lsblk_stick(**child_changes: Any) -> dict[str, Any]:
    """Return the lsblk entry of a FAT32 stick with one partition."""
    child = {
        "name": "sdb1",
        "type": "part",
        "partn": 1,
        "partflags": None,
        "parttype": "0xc",
        "mountpoints": [MOUNT_PATH],
    }
    child.update(child_changes)
    return {"name": "sdb", "type": "disk", "mountpoints": [None], "children": [child]}


class RecordingRunner:
    """Fake command runner that records calls and replays fixed output."""

    def __init__(self, outputs: dict[str, str], failing: frozenset[str] = frozenset()) -> None:
        self.outputs = outputs
        self.failing = failing
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        program = cmd[0]
        return_code = 1 if program in self.failing else 0
        return subprocess.CompletedProcess(cmd, return_code, self.outputs.get(program, ""), "")


def build_linux_probe(
    devices: list[dict[str, Any]],
    mount_table: str = WRITABLE_MOUNT_TABLE,
    failing: frozenset[str] = frozenset(),
) -> tuple[LinuxPreflightProbe, RecordingRunner]:
    """Return a Linux probe over fixed lsblk, udevadm, and mount table output."""
    outputs = {"lsblk": json.dumps({"blockdevices": devices}), "udevadm": UDEV_PROPERTIES}
    runner = RecordingRunner(outputs, failing)
    return LinuxPreflightProbe(runner, lambda: mount_table), runner


class MbrClassificationTests(unittest.TestCase):
    """Primary slots hold numbers 1 to 4. Extended containers are not primary."""

    def test_classification_table(self) -> None:
        cases = (
            (1, 0x0C, True),
            (4, 0x0B, True),
            (1, 0x05, False),
            (2, 0x0F, False),
            (3, 0x85, False),
            (5, 0x0C, False),
            (1, None, None),
            (None, 0x0C, None),
            (0, 0x0C, None),
        )
        for partition_number, type_code, expected in cases:
            with self.subTest(partition_number=partition_number, type_code=type_code):
                self.assertIs(classify_mbr_partition(partition_number, type_code), expected)


class ApplyDriveFactsTests(unittest.TestCase):
    """Probe facts land on the matching partition only."""

    def test_facts_apply_by_partition_number(self) -> None:
        facts = DriveFacts(
            is_system=False,
            hardware_id="abcd:1234",
            partitions={1: PartitionFacts(is_active=True, is_primary=True)},
            inferred_facts=frozenset({FACT_PRIMARY_PARTITION}),
        )
        result = apply_drive_facts(build_unprobed_inspection(), facts)
        self.assertIs(result.drive.is_system, False)
        self.assertEqual(result.drive.hardware_id, "abcd:1234")
        self.assertIs(result.partitions[0].is_active, True)
        self.assertEqual(result.inferred_facts, frozenset({FACT_PRIMARY_PARTITION}))

    def test_partition_without_facts_stays_unchanged(self) -> None:
        facts = DriveFacts(is_system=False, partitions={7: PartitionFacts(is_active=True)})
        result = apply_drive_facts(build_unprobed_inspection(), facts)
        self.assertIsNone(result.partitions[0].is_active)


class LinuxProbeTests(unittest.TestCase):
    """The Linux probe reads lsblk, udevadm, and the mount table."""

    def test_stick_facts(self) -> None:
        probe, runner = build_linux_probe([build_lsblk_stick()])
        result = probe.probe(build_unprobed_inspection())
        partition = result.partitions[0]
        self.assertIs(result.drive.is_system, False)
        self.assertEqual(result.drive.hardware_id, "0781:5581")
        self.assertIs(partition.is_active, False)
        self.assertIs(partition.is_primary, True)
        self.assertIs(partition.filesystem_health, FilesystemHealth.HEALTHY)
        self.assertEqual(result.inferred_facts, frozenset({FACT_FILESYSTEM_HEALTH}))
        self.assertEqual(
            runner.calls, [list(LSBLK_FACTS_COMMAND), [*UDEVADM_COMMAND_PREFIX, "/dev/sdb"]]
        )

    def test_probed_stick_passes_with_inferred_health(self) -> None:
        probe, _runner = build_linux_probe([build_lsblk_stick()])
        result = evaluate_preflight(probe.probe(build_unprobed_inspection()), build_request())
        self.assertTrue(result.passed)
        self.assertEqual(result.unverified_facts, (FACT_FILESYSTEM_HEALTH,))

    def test_active_flag_is_read(self) -> None:
        probe, _runner = build_linux_probe([build_lsblk_stick(partflags="0x80")])
        result = probe.probe(build_unprobed_inspection())
        self.assertIs(result.partitions[0].is_active, True)
        refusals = evaluate_preflight(result, build_request()).refusals
        self.assertEqual([refusal.code for refusal in refusals], [RefusalCode.ACTIVE_PARTITION])

    def test_logical_and_extended_partitions_are_not_primary(self) -> None:
        for changes in ({"partn": 5}, {"parttype": "0x5"}):
            with self.subTest(changes=changes):
                probe, _runner = build_linux_probe([build_lsblk_stick(**changes)])
                number = changes.get("partn", 1)
                result = probe.probe(build_unprobed_inspection(partition_number=number))
                self.assertIs(result.partitions[0].is_primary, False)

    def test_system_mounts_mark_the_drive(self) -> None:
        for mount in ("/", "/boot", "/boot/efi", "/efi", "[SWAP]"):
            with self.subTest(mount=mount):
                nested = {"name": "luks-1", "type": "crypt", "mountpoints": [mount]}
                device = build_lsblk_stick(children=[nested])
                probe, _runner = build_linux_probe([device])
                self.assertIs(probe.probe(build_unprobed_inspection()).drive.is_system, True)

    def test_read_only_mount_on_writable_media_is_an_error(self) -> None:
        probe, _runner = build_linux_probe([build_lsblk_stick()], READ_ONLY_MOUNT_TABLE)
        result = probe.probe(build_unprobed_inspection())
        self.assertIs(result.partitions[0].filesystem_health, FilesystemHealth.ERRORS)
        refusals = evaluate_preflight(result, build_request()).refusals
        self.assertEqual([refusal.code for refusal in refusals], [RefusalCode.FILESYSTEM_ERRORS])

    def test_unreadable_mount_table_leaves_health_unverified(self) -> None:
        def raise_os_error() -> str:
            raise OSError("mount table unavailable")

        runner = RecordingRunner(
            {"lsblk": json.dumps({"blockdevices": [build_lsblk_stick()]}), "udevadm": ""}
        )
        result = LinuxPreflightProbe(runner, raise_os_error).probe(build_unprobed_inspection())
        self.assertIsNone(result.partitions[0].filesystem_health)
        self.assertEqual(result.inferred_facts, frozenset())

    def test_udevadm_failure_leaves_hardware_id_absent(self) -> None:
        probe, _runner = build_linux_probe([build_lsblk_stick()], failing=frozenset({"udevadm"}))
        self.assertIsNone(probe.probe(build_unprobed_inspection()).drive.hardware_id)

    def test_unsafe_device_path_never_reaches_a_command(self) -> None:
        probe, runner = build_linux_probe([build_lsblk_stick()])
        inspection = build_unprobed_inspection()
        unsafe = build_inspection(
            drive=build_drive(path="/dev/../etc/passwd", is_system=None),
            partitions=inspection.partitions,
        )
        self.assertIsNone(probe.probe(unsafe).drive.hardware_id)
        self.assertEqual(runner.calls, [list(LSBLK_FACTS_COMMAND)])

    def test_missing_drive_and_failed_lsblk_raise(self) -> None:
        probe, _runner = build_linux_probe([])
        with self.assertRaises(CommandExecutionError):
            probe.probe(build_unprobed_inspection())
        failing_probe, _runner = build_linux_probe([], failing=frozenset({"lsblk"}))
        with self.assertRaises(CommandExecutionError):
            failing_probe.probe(build_unprobed_inspection())

    def test_malformed_lsblk_output_raises(self) -> None:
        for output in ("{not json", json.dumps({"other": []}), json.dumps([1, 2])):
            with self.subTest(output=output):
                probe = LinuxPreflightProbe(RecordingRunner({"lsblk": output}), lambda: "")
                with self.assertRaises(InspectionParseError):
                    probe.probe(build_unprobed_inspection())


def build_windows_inspection() -> DriveInspection:
    """Return the unprobed stick as the Windows inspector names it."""
    partition = build_partition(
        name="disk2part1",
        mount_points=(WINDOWS_MOUNT,),
        is_active=None,
        is_primary=None,
        filesystem_health=None,
    )
    drive = build_drive(name="disk2", hardware_id=None, is_system=None)
    return build_inspection(drive=drive, partitions=(partition,))


class WindowsRunner:
    """Fake runner that replays one JSON document per fixed PowerShell command."""

    def __init__(self, disk: Any, partition: Any, volume: Any) -> None:
        self.outputs = {
            DISK_FACTS_COMMAND: json.dumps(disk),
            PARTITION_FACTS_COMMAND: json.dumps(partition),
            VOLUME_HEALTH_COMMAND: json.dumps(volume),
        }
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(cmd))
        return subprocess.CompletedProcess(cmd, 0, self.outputs[tuple(cmd)], "")


def probe_windows(
    disk_changes: dict[str, Any] | None = None,
    partition_changes: dict[str, Any] | None = None,
    health: Any = "Healthy",
) -> tuple[DriveInspection, WindowsRunner]:
    """Probe the Windows stick over fixed cmdlet output with the given changes."""
    disk: dict[str, Any] = {"DiskNumber": 2, "UniqueId": "USBSTOR\\DISK&VEN_SANDISK"}
    disk.update({"IsBoot": False, "IsSystem": False})
    disk.update(disk_changes or {})
    partition: dict[str, Any] = {"DiskNumber": 2, "PartitionNumber": 1, "IsActive": False}
    partition.update({"IsBoot": False, "IsSystem": False, "MbrType": 12, "DriveLetter": "E"})
    partition.update(partition_changes or {})
    other_disk = {"DiskNumber": 0, "UniqueId": "SYSTEM", "IsBoot": True, "IsSystem": True}
    volume = {"DriveLetter": "E", "HealthStatus": health}
    runner = WindowsRunner([other_disk, disk], partition, volume)
    return WindowsPreflightProbe(runner).probe(build_windows_inspection()), runner


class WindowsProbeTests(unittest.TestCase):
    """The Windows probe reads three fixed Storage cmdlet projections."""

    def test_stick_facts(self) -> None:
        result, runner = probe_windows()
        partition = result.partitions[0]
        self.assertIs(result.drive.is_system, False)
        self.assertEqual(result.drive.hardware_id, "USBSTOR\\DISK&VEN_SANDISK")
        self.assertIs(partition.is_active, False)
        self.assertIs(partition.is_primary, True)
        self.assertIs(partition.filesystem_health, FilesystemHealth.HEALTHY)
        self.assertEqual(result.inferred_facts, frozenset({FACT_PRIMARY_PARTITION}))
        self.assertEqual(
            runner.calls, [DISK_FACTS_COMMAND, PARTITION_FACTS_COMMAND, VOLUME_HEALTH_COMMAND]
        )

    def test_commands_are_fixed_and_hold_no_drive_data(self) -> None:
        prefix = (POWERSHELL_EXE, "-NoProfile", "-NonInteractive", "-Command")
        for command in (DISK_FACTS_COMMAND, PARTITION_FACTS_COMMAND, VOLUME_HEALTH_COMMAND):
            with self.subTest(command=command[-1]):
                self.assertEqual(command[:4], prefix)
                self.assertNotIn("disk2", command[-1])

    def test_health_status_values(self) -> None:
        cases = (
            ("Healthy", FilesystemHealth.HEALTHY),
            (0, FilesystemHealth.HEALTHY),
            ("Scan Needed", FilesystemHealth.ERRORS),
            ("Full Repair Needed", FilesystemHealth.ERRORS),
            (2, FilesystemHealth.ERRORS),
            ("Unrecognized", None),
            (None, None),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                result, _runner = probe_windows(health=status)
                self.assertIs(result.partitions[0].filesystem_health, expected)

    def test_boot_and_system_flags_mark_the_drive(self) -> None:
        for disk_changes, partition_changes in (
            ({"IsBoot": True}, None),
            ({"IsSystem": True}, None),
            (None, {"IsBoot": True}),
            (None, {"IsSystem": True}),
        ):
            with self.subTest(disk=disk_changes, partition=partition_changes):
                result, _runner = probe_windows(disk_changes, partition_changes)
                self.assertIs(result.drive.is_system, True)

    def test_absent_disk_flags_leave_system_state_unknown(self) -> None:
        result, _runner = probe_windows({"IsBoot": None})
        self.assertIsNone(result.drive.is_system)

    def test_active_and_extended_partitions_are_read(self) -> None:
        result, _runner = probe_windows(partition_changes={"IsActive": True, "MbrType": 5})
        self.assertIs(result.partitions[0].is_active, True)
        self.assertIs(result.partitions[0].is_primary, False)

    def test_missing_disk_and_foreign_name_raise(self) -> None:
        runner = WindowsRunner([], [], [])
        with self.assertRaises(CommandExecutionError):
            WindowsPreflightProbe(runner).probe(build_windows_inspection())
        with self.assertRaises(CommandExecutionError):
            WindowsPreflightProbe(runner).probe(build_unprobed_inspection())


class MacOSProbeTests(unittest.TestCase):
    """The macOS probe reads diskutil info and the inspected mount points."""

    def probe_with(
        self, info: dict[str, Any], mount_point: str = "/Volumes/STICK"
    ) -> DriveInspection:
        """Probe a macOS stick with the given diskutil info and mount point."""
        partition = build_partition(
            name="disk4s1",
            mount_points=(mount_point,),
            is_active=None,
            is_primary=None,
            filesystem_health=None,
        )
        drive = build_drive(name="disk4", hardware_id=None, is_system=None)
        inspection = build_inspection(drive=drive, partitions=(partition,))
        return MacOSPreflightProbe(lambda _identifier: info).probe(inspection)

    def test_stick_facts(self) -> None:
        result = self.probe_with({"MediaName": "SanDisk Ultra", "SystemImage": False})
        self.assertIs(result.drive.is_system, False)
        self.assertEqual(result.drive.hardware_id, "SanDisk Ultra")

    def test_partition_facts_stay_unverified(self) -> None:
        result = self.probe_with({"MediaName": "SanDisk Ultra"})
        partition = result.partitions[0]
        self.assertIsNone(partition.is_active)
        self.assertIsNone(partition.is_primary)
        self.assertIsNone(partition.filesystem_health)
        self.assertEqual(result.inferred_facts, frozenset())

    def test_unverified_facts_reach_the_preflight_result(self) -> None:
        partition = build_partition(is_active=None, is_primary=None, filesystem_health=None)
        drive = build_drive(name="disk4", hardware_id=None, is_system=None)
        inspection = build_inspection(drive=drive, partitions=(partition,))
        probed = MacOSPreflightProbe(lambda _identifier: {}).probe(inspection)
        result = evaluate_preflight(probed, build_request())
        self.assertTrue(result.passed)
        self.assertEqual(
            result.unverified_facts,
            (FACT_ACTIVE_PARTITION, FACT_FILESYSTEM_HEALTH, FACT_PRIMARY_PARTITION),
        )

    def test_system_mounts_and_system_image_mark_the_drive(self) -> None:
        self.assertIs(self.probe_with({}, "/").drive.is_system, True)
        self.assertIs(self.probe_with({}, "/System/Volumes/Data").drive.is_system, True)
        self.assertIs(self.probe_with({"SystemImage": True}).drive.is_system, True)

    def test_hardware_id_falls_back_to_the_registry_name(self) -> None:
        result = self.probe_with({"IORegistryEntryName": "Ultra Media"})
        self.assertEqual(result.drive.hardware_id, "Ultra Media")
        self.assertIsNone(self.probe_with({}).drive.hardware_id)

    def test_foreign_drive_name_never_reaches_diskutil(self) -> None:
        calls: list[str] = []

        def record_call(identifier: str) -> dict[str, Any]:
            calls.append(identifier)
            return {}

        with self.assertRaises(CommandExecutionError):
            MacOSPreflightProbe(record_call).probe(build_unprobed_inspection())
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
