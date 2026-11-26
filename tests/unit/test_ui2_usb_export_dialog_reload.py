"""Tests that a rescan in the USB export dialog drops the drive shown on Confirm."""

from __future__ import annotations

from typing import Any

from infiniteprep.services.usb.models import DriveInspectionError
from infiniteprep.services.usb.targets import TargetCandidate
from infiniteprep.ui.usb_export_dialog import UsbExportDialog
from tests.unit.usb_preflight_support import build_inspection, build_request


def test_rescan_drops_the_confirmed_drive(qtbot: Any) -> None:
    """After Back and a rescan, no drive counts as confirmed until Next runs again."""
    inspection = build_inspection()
    candidate = TargetCandidate(name=inspection.drive.name, inspection=inspection, error=None)
    dialog = UsbExportDialog(lambda: (candidate,), build_request())
    qtbot.addWidget(dialog)
    dialog.drive_list.setCurrentRow(0)
    dialog.next_btn.click()
    confirmed_before = dialog.selected_inspection()

    dialog.back_btn.click()
    dialog.rescan_btn.click()

    assert confirmed_before == inspection
    assert dialog.selected_inspection() is None


def test_failed_rescan_drops_confirmed_drive(qtbot: Any) -> None:
    """A rescan that fails to list drives also drops the confirmed drive."""
    inspection = build_inspection()
    candidate = TargetCandidate(name=inspection.drive.name, inspection=inspection, error=None)
    calls: list[int] = []

    def load() -> tuple[TargetCandidate, ...]:
        """List the drive once, then fail as a lost drive listing does."""
        calls.append(1)
        if len(calls) > 1:
            raise DriveInspectionError("lsblk: raw output")
        return (candidate,)

    dialog = UsbExportDialog(load, build_request())
    qtbot.addWidget(dialog)
    dialog.drive_list.setCurrentRow(0)
    dialog.next_btn.click()
    dialog.back_btn.click()

    dialog.rescan_btn.click()

    assert dialog.selected_inspection() is None
