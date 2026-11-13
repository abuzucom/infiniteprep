"""Tests for the bus-only USB drive class rule and the fixed-disk note (D131, D188, D372)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from infiniteprep.services.usb.models import DriveInspection
from infiniteprep.services.usb.preflight import (
    FIXED_DISK_NOTE_TEXT,
    SAFETY_REFUSAL_CODES,
    NoteCode,
    PreflightResult,
    RefusalCode,
    evaluate_preflight,
    is_supported_bus_class,
)
from infiniteprep.services.usb.targets import load_removable_targets
from tests.unit.usb_preflight_support import build_drive, build_inspection, build_request

PROBED_HARDWARE_ID = "probed:0002"
SUPPORTED_BUSES = ("usb", "USB", "sd", "MMC", "Secure Digital")
UNSUPPORTED_BUSES = ("sata", "nvme", "thunderbolt", None)


def evaluate(inspection: DriveInspection) -> PreflightResult:
    """Evaluate an inspection against the passing request with no reserve."""
    return evaluate_preflight(inspection, build_request())


def note_codes(result: PreflightResult) -> tuple[NoteCode, ...]:
    """Return the note codes of a result in order."""
    return tuple(note.code for note in result.notes)


class FixedInspector:
    """Fake inspector that returns fixed inspections."""

    def __init__(self, inspections: tuple[DriveInspection, ...]) -> None:
        self.inspections = inspections

    def inspect_all(self) -> tuple[DriveInspection, ...]:
        return self.inspections


class RecordingProbe:
    """Fake probe that records drive names and marks each probed inspection."""

    def __init__(self) -> None:
        self.probed_names: list[str] = []

    def probe(self, inspection: DriveInspection) -> DriveInspection:
        self.probed_names.append(inspection.drive.name)
        return replace(inspection, drive=replace(inspection.drive, hardware_id=PROBED_HARDWARE_ID))


@pytest.mark.parametrize("bus_type", SUPPORTED_BUSES)
@pytest.mark.parametrize("is_removable", [True, False])
def test_supported_bus_passes_the_class_rule(bus_type: str, is_removable: bool) -> None:
    """A USB, SD, or MMC bus passes with or without the removable flag."""
    assert is_supported_bus_class(build_drive(bus_type=bus_type, is_removable=is_removable))


@pytest.mark.parametrize("bus_type", UNSUPPORTED_BUSES)
@pytest.mark.parametrize("is_removable", [True, False])
def test_other_bus_fails_the_class_rule(bus_type: str | None, is_removable: bool) -> None:
    """Any other bus fails, even with the removable flag."""
    assert not is_supported_bus_class(build_drive(bus_type=bus_type, is_removable=is_removable))


def test_fixed_usb_disk_passes_with_the_fixed_disk_note() -> None:
    """A USB disk without the removable flag passes and carries one fixed-disk note."""
    result = evaluate(build_inspection(drive=build_drive(is_removable=False)))

    assert result.passed
    assert note_codes(result) == (NoteCode.FIXED_DISK,)
    assert result.notes[0].message == FIXED_DISK_NOTE_TEXT


def test_removable_usb_stick_has_no_notes() -> None:
    """A USB stick with the removable flag passes without notes."""
    result = evaluate(build_inspection())

    assert result.passed
    assert result.notes == ()


@pytest.mark.parametrize("bus_type", UNSUPPORTED_BUSES)
def test_non_usb_drive_is_refused_with_the_bus_code(bus_type: str | None) -> None:
    """A drive on another bus refuses with the unsupported-bus code alone."""
    result = evaluate(build_inspection(drive=build_drive(bus_type=bus_type)))

    assert [refusal.code for refusal in result.refusals] == [RefusalCode.UNSUPPORTED_BUS]
    assert result.notes == ()


def test_unsupported_bus_is_a_safety_refusal() -> None:
    """The bus refusal hides every other rule, as the earlier class refusal did."""
    assert RefusalCode.UNSUPPORTED_BUS in SAFETY_REFUSAL_CODES


def test_fixed_usb_system_disk_is_refused() -> None:
    """A USB-booted system disk refuses. The bus rule never overrides D131."""
    inspection = build_inspection(drive=build_drive(is_removable=False, is_system=True))
    result = evaluate(inspection)

    assert [refusal.code for refusal in result.refusals] == [RefusalCode.SYSTEM_VOLUME]


def test_non_usb_system_disk_returns_both_safety_refusals() -> None:
    """An internal system disk refuses on the bus and on the system state."""
    drive = build_drive(bus_type="nvme", is_removable=False, is_system=True)
    result = evaluate(build_inspection(drive=drive))

    assert [refusal.code for refusal in result.refusals] == [
        RefusalCode.UNSUPPORTED_BUS,
        RefusalCode.SYSTEM_VOLUME,
    ]


def test_loader_lists_and_probes_fixed_usb_disks() -> None:
    """The loader keeps USB fixed disks and drops drives on other buses."""
    inspector = FixedInspector(
        (
            build_inspection(drive=build_drive(name="nvme0n1", bus_type="nvme")),
            build_inspection(drive=build_drive(name="sdb")),
            build_inspection(drive=build_drive(name="sdc", is_removable=False)),
        )
    )
    probe = RecordingProbe()

    candidates = load_removable_targets(inspector, probe)

    assert probe.probed_names == ["sdb", "sdc"]
    assert [candidate.name for candidate in candidates] == ["sdb", "sdc"]
    assert all(candidate.error is None for candidate in candidates)
