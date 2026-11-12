"""Tests for the USB export drive picker dialog (D93, D188)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from infiniteprep.services.usb.models import CommandExecutionError, DriveInspection
from infiniteprep.services.usb.targets import INSPECTION_FAILED_TEXT, TargetCandidate
from infiniteprep.ui.usb_drive_picker_dialog import (
    LOAD_FAILED_TEXT,
    NO_DRIVES_TEXT,
    UsbDrivePickerDialog,
)
from tests.unit.usb_preflight_support import (
    MOUNT_PATH,
    build_drive,
    build_inspection,
    build_partition,
)

FAILED_ROW = 2
RESCAN_LOAD_COUNT = 2


def build_candidate(inspection: DriveInspection) -> TargetCandidate:
    """Wrap a probed inspection as a loaded target candidate."""
    return TargetCandidate(name=inspection.drive.name, inspection=inspection, error=None)


def build_candidates() -> tuple[TargetCandidate, ...]:
    """Return a passing stick, a refused exFAT stick, and a drive whose probe failed."""
    refused = build_inspection(
        drive=build_drive(name="sdc"),
        partitions=(build_partition(filesystem="exfat", label="EXFAT"),),
    )
    failed = TargetCandidate(name="sdd", inspection=None, error=INSPECTION_FAILED_TEXT)
    return (build_candidate(build_inspection()), build_candidate(refused), failed)


class CountingLoader:
    """Fake target loader that counts calls and returns fixed candidates."""

    def __init__(self, candidates: tuple[TargetCandidate, ...]) -> None:
        self.candidates = candidates
        self.call_count = 0

    def __call__(self) -> tuple[TargetCandidate, ...]:
        self.call_count += 1
        return self.candidates


def open_picker(qtbot, candidates: tuple[TargetCandidate, ...]) -> UsbDrivePickerDialog:
    """Open the picker over fixed candidates."""
    dialog = UsbDrivePickerDialog(CountingLoader(candidates))
    qtbot.addWidget(dialog)
    return dialog


def test_each_candidate_gets_a_row(qtbot) -> None:
    """The list shows the label and mount path of every loaded drive."""
    dialog = open_picker(qtbot, build_candidates())

    assert dialog.drive_list.count() == len(build_candidates())
    assert "STICK" in dialog.drive_list.item(0).text()
    assert MOUNT_PATH in dialog.drive_list.item(0).text()


def test_refused_drive_shows_its_first_reason(qtbot) -> None:
    """A refused removable drive stays in the list with its refusal message."""
    dialog = open_picker(qtbot, build_candidates())

    assert "EXFAT" in dialog.drive_list.item(1).text()
    assert "The filesystem is not FAT32." in dialog.drive_list.item(1).text()
    assert "The filesystem is not FAT32." not in dialog.drive_list.item(0).text()


def test_failed_drive_row_is_disabled(qtbot) -> None:
    """A drive whose probe failed shows fixed text and cannot be selected."""
    dialog = open_picker(qtbot, build_candidates())
    failed_item = dialog.drive_list.item(FAILED_ROW)

    assert "sdd" in failed_item.text()
    assert INSPECTION_FAILED_TEXT in failed_item.text()
    assert not failed_item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert dialog.drive_list.item(0).flags() & Qt.ItemFlag.ItemIsEnabled


def test_select_needs_a_chosen_row(qtbot) -> None:
    """The select button stays disabled until a drive row is chosen."""
    dialog = open_picker(qtbot, build_candidates())
    assert not dialog.select_btn.isEnabled()
    assert dialog.selected_inspection() is None

    dialog.drive_list.setCurrentRow(0)

    assert dialog.select_btn.isEnabled()


def test_select_returns_the_chosen_inspection(qtbot) -> None:
    """Selecting a row and confirming yields the inspection of that drive."""
    candidates = build_candidates()
    dialog = open_picker(qtbot, candidates)

    dialog.drive_list.setCurrentRow(1)
    dialog.select_btn.click()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_inspection() is candidates[1].inspection


def test_cancel_rejects(qtbot) -> None:
    """Cancel closes the picker as rejected."""
    dialog = open_picker(qtbot, build_candidates())

    dialog.cancel_btn.click()

    assert dialog.result() == QDialog.DialogCode.Rejected


def test_empty_result_shows_a_note(qtbot) -> None:
    """Without removable drives the picker shows a note and no rows."""
    dialog = open_picker(qtbot, ())

    assert dialog.drive_list.count() == 0
    assert dialog.status_label.text() == NO_DRIVES_TEXT
    assert not dialog.select_btn.isEnabled()


def test_failed_load_shows_fixed_text(qtbot) -> None:
    """A failed drive listing shows fixed text. Raw command errors stay out of the dialog."""

    def raise_listing_error() -> tuple[TargetCandidate, ...]:
        raise CommandExecutionError("lsblk failed with secret detail")

    dialog = UsbDrivePickerDialog(raise_listing_error)
    qtbot.addWidget(dialog)

    assert dialog.drive_list.count() == 0
    assert dialog.status_label.text() == LOAD_FAILED_TEXT
    assert "secret detail" not in dialog.status_label.text()
    assert dialog.status_label.textFormat() == Qt.TextFormat.PlainText


def test_rescan_loads_again_and_clears_the_selection(qtbot) -> None:
    """Rescan calls the loader again and drops the chosen row."""
    loader = CountingLoader(build_candidates())
    dialog = UsbDrivePickerDialog(loader)
    qtbot.addWidget(dialog)
    dialog.drive_list.setCurrentRow(0)

    dialog.rescan_btn.click()

    assert loader.call_count == RESCAN_LOAD_COUNT
    assert dialog.drive_list.count() == len(build_candidates())
    assert dialog.selected_inspection() is None
    assert not dialog.select_btn.isEnabled()
