"""Tests for the removable-class USB target loader (D131, D188)."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from infiniteprep.services.usb.linux import LinuxDriveInspector
from infiniteprep.services.usb.linux_probe import LinuxPreflightProbe
from infiniteprep.services.usb.macos import MacOSDriveInspector
from infiniteprep.services.usb.macos_probe import MacOSPreflightProbe
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveInspection,
    DriveInspectionError,
)
from infiniteprep.services.usb.preflight import is_removable_class
from infiniteprep.services.usb.targets import (
    INSPECTION_FAILED_TEXT,
    build_platform_tools,
    load_removable_targets,
)
from infiniteprep.services.usb.windows import WindowsDriveInspector
from infiniteprep.services.usb.windows_probe import WindowsPreflightProbe
from tests.unit.usb_preflight_support import build_drive, build_inspection

PROBED_HARDWARE_ID = "probed:0001"


class FixedInspector:
    """Fake inspector that returns fixed inspections."""

    def __init__(self, inspections: tuple[DriveInspection, ...]) -> None:
        self.inspections = inspections

    def inspect_all(self) -> tuple[DriveInspection, ...]:
        return self.inspections


class RecordingProbe:
    """Fake probe that records drive names and marks each probed inspection."""

    def __init__(self, failing_names: frozenset[str] = frozenset()) -> None:
        self.failing_names = failing_names
        self.probed_names: list[str] = []

    def probe(self, inspection: DriveInspection) -> DriveInspection:
        self.probed_names.append(inspection.drive.name)
        if inspection.drive.name in self.failing_names:
            raise CommandExecutionError("probe command failed")
        return replace(inspection, drive=replace(inspection.drive, hardware_id=PROBED_HARDWARE_ID))


def build_named_inspection(name: str, **drive_changes: object) -> DriveInspection:
    """Return the passing stick under another drive name with drive fields replaced."""
    return build_inspection(drive=build_drive(name=name, **drive_changes))


def test_removable_class_needs_the_flag_and_a_removable_bus() -> None:
    """A drive counts as removable-class only with the flag and a USB, SD, or MMC bus."""
    assert is_removable_class(build_drive())
    assert is_removable_class(build_drive(bus_type="SD"))
    assert not is_removable_class(build_drive(is_removable=False))
    assert not is_removable_class(build_drive(bus_type="sata"))
    assert not is_removable_class(build_drive(bus_type=None))


def test_only_removable_class_drives_are_probed_and_listed() -> None:
    """Internal disks and fixed USB disks never reach the probe or the result."""
    inspector = FixedInspector(
        (
            build_named_inspection("sda", is_removable=False, bus_type="sata"),
            build_named_inspection("sdb"),
            build_named_inspection("sdc", is_removable=False),
        )
    )
    probe = RecordingProbe()

    candidates = load_removable_targets(inspector, probe)

    assert probe.probed_names == ["sdb"]
    assert [candidate.name for candidate in candidates] == ["sdb"]
    assert candidates[0].error is None
    assert candidates[0].inspection is not None
    assert candidates[0].inspection.drive.hardware_id == PROBED_HARDWARE_ID


def test_failed_probe_lists_the_drive_without_hiding_the_rest() -> None:
    """One failed probe yields an error entry. Other drives still load."""
    inspector = FixedInspector((build_named_inspection("sdb"), build_named_inspection("sdc")))
    probe = RecordingProbe(failing_names=frozenset({"sdb"}))

    candidates = load_removable_targets(inspector, probe)

    assert [candidate.name for candidate in candidates] == ["sdb", "sdc"]
    assert candidates[0].inspection is None
    assert candidates[0].error == INSPECTION_FAILED_TEXT
    assert candidates[1].inspection is not None
    assert candidates[1].error is None


def test_failed_probe_error_text_holds_no_command_output() -> None:
    """The error entry carries fixed text. Raw command errors stay out of the list."""
    inspector = FixedInspector((build_named_inspection("sdb"),))
    probe = RecordingProbe(failing_names=frozenset({"sdb"}))

    candidates = load_removable_targets(inspector, probe)

    assert "probe command failed" not in (candidates[0].error or "")


def test_failed_inspection_raises() -> None:
    """A failed drive listing raises. The caller reports it."""

    class FailingInspector:
        def inspect_all(self) -> tuple[DriveInspection, ...]:
            raise CommandExecutionError("listing failed")

    with pytest.raises(DriveInspectionError):
        load_removable_targets(FailingInspector(), RecordingProbe())


def test_platform_tools_match_the_platform() -> None:
    """Linux and Windows get their own inspector and probe."""
    linux_inspector, linux_probe = build_platform_tools("linux")
    windows_inspector, windows_probe = build_platform_tools("win32")

    assert isinstance(linux_inspector, LinuxDriveInspector)
    assert isinstance(linux_probe, LinuxPreflightProbe)
    assert isinstance(windows_inspector, WindowsDriveInspector)
    assert isinstance(windows_probe, WindowsPreflightProbe)


@pytest.mark.skipif(sys.platform != "darwin", reason="The macOS inspector needs macOS.")
def test_platform_tools_on_macos() -> None:
    """macOS gets the diskutil inspector and probe."""
    inspector, probe = build_platform_tools("darwin")

    assert isinstance(inspector, MacOSDriveInspector)
    assert isinstance(probe, MacOSPreflightProbe)


def test_unknown_platform_is_rejected() -> None:
    """A platform without an inspector raises a drive inspection error."""
    with pytest.raises(DriveInspectionError):
        build_platform_tools("sunos5")
