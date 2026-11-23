"""Tests for a drive row in the USB export dialog that has no inspection."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from infiniteprep.services.usb.targets import TargetCandidate
from infiniteprep.ui.usb_export_dialog import PAGE_CHOOSE, UsbExportDialog
from tests.unit.usb_preflight_support import build_inspection, build_request

INSPECTION_FAILED_TEXT = "Couldn't read this drive."


def test_uninspected_drive_cannot_be_picked(qtbot: Any) -> None:
    """A drive without an inspection shows a disabled row, and Next stays off for it."""
    inspection = build_inspection()
    failed = TargetCandidate(name="sdd", inspection=None, error=INSPECTION_FAILED_TEXT)
    passing = TargetCandidate(name=inspection.drive.name, inspection=inspection, error=None)
    dialog = UsbExportDialog(lambda: (failed, passing), build_request())
    qtbot.addWidget(dialog)
    failed_row = dialog.drive_list.item(0)

    dialog.drive_list.setCurrentRow(0)
    next_after_failed = dialog.next_btn.isEnabled()
    dialog.next_btn.click()

    assert not failed_row.flags() & Qt.ItemFlag.ItemIsEnabled
    assert not failed_row.flags() & Qt.ItemFlag.ItemIsSelectable
    assert INSPECTION_FAILED_TEXT in failed_row.text()
    assert not next_after_failed
    assert dialog.pages.currentIndex() == PAGE_CHOOSE
    assert dialog.selected_inspection() is None
    dialog.drive_list.setCurrentRow(1)
    assert dialog.next_btn.isEnabled()
    assert dialog.chosen_inspection() == inspection
