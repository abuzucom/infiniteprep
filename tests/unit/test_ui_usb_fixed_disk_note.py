"""Tests for the fixed-disk note in the USB confirm dialog and drive picker (D372)."""

from __future__ import annotations

from infiniteprep.services.usb.models import DriveInspection
from infiniteprep.services.usb.preflight import FIXED_DISK_NOTE_TEXT, evaluate_preflight
from infiniteprep.services.usb.targets import TargetCandidate
from infiniteprep.ui.usb_drive_picker_dialog import UsbDrivePickerDialog, describe_candidate
from infiniteprep.ui.usb_preflight_dialog import UsbPreflightDialog
from tests.unit.usb_preflight_support import build_drive, build_inspection, build_request


def build_fixed_inspection() -> DriveInspection:
    """Return the passing USB stick reported as a fixed disk."""
    return build_inspection(drive=build_drive(is_removable=False))


def open_confirm_dialog(qtbot, inspection: DriveInspection) -> UsbPreflightDialog:
    """Evaluate the preflight and open the confirm dialog for the result."""
    request = build_request()
    dialog = UsbPreflightDialog(inspection, request, evaluate_preflight(inspection, request))
    qtbot.addWidget(dialog)
    return dialog


def build_candidate(inspection: DriveInspection) -> TargetCandidate:
    """Wrap a probed inspection as a loaded target candidate."""
    return TargetCandidate(name=inspection.drive.name, inspection=inspection, error=None)


def test_confirm_dialog_lists_the_fixed_disk_note(qtbot) -> None:
    """A fixed USB disk shows the note and stays confirmable."""
    dialog = open_confirm_dialog(qtbot, build_fixed_inspection())

    assert dialog.notes_list.count() == 1
    assert dialog.notes_list.item(0).text() == FIXED_DISK_NOTE_TEXT
    assert dialog.confirm_btn.isEnabled()


def test_confirm_dialog_shows_no_note_for_a_removable_stick(qtbot) -> None:
    """A stick with the removable flag shows an empty notes list."""
    dialog = open_confirm_dialog(qtbot, build_inspection())

    assert dialog.notes_list.count() == 0


def test_picker_row_carries_the_fixed_disk_note() -> None:
    """The picker row of a fixed USB disk names the note after the mount path."""
    row = describe_candidate(build_candidate(build_fixed_inspection()))

    assert "STICK" in row
    assert FIXED_DISK_NOTE_TEXT in row


def test_picker_row_of_a_removable_stick_has_no_note() -> None:
    """A stick with the removable flag gets no note in its row."""
    row = describe_candidate(build_candidate(build_inspection()))

    assert FIXED_DISK_NOTE_TEXT not in row


def test_picker_lets_a_fixed_usb_disk_be_selected(qtbot) -> None:
    """A fixed USB disk row is enabled and selectable."""
    inspection = build_fixed_inspection()
    dialog = UsbDrivePickerDialog(lambda: (build_candidate(inspection),))
    qtbot.addWidget(dialog)

    dialog.drive_list.setCurrentRow(0)

    assert dialog.select_btn.isEnabled()
    assert dialog.selected_inspection() is inspection
