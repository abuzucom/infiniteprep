"""Tests for the two-step USB export dialog: Choose drive, then Confirm."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QListWidget

from infiniteprep.services.usb.models import (
    DriveInspection,
    DriveInspectionError,
    FilesystemHealth,
)
from infiniteprep.services.usb.preflight import PreflightRequest, evaluate_preflight
from infiniteprep.services.usb.targets import TargetCandidate
from infiniteprep.ui.usb_export_dialog import (
    CHOOSE_PROMPT_TEXT,
    LOAD_FAILED_TEXT,
    NO_DRIVES_TEXT,
    PAGE_CHOOSE,
    PAGE_CONFIRM,
    UsbExportDialog,
)
from infiniteprep.ui.usb_export_model import (
    NO_DRIVE_CHANGES_TEXT,
    SPACE_BAR_STEPS,
    needed_bytes,
    space_bar_value,
    space_summary,
)
from tests.unit.usb_preflight_support import (
    FREE_BYTES,
    build_drive,
    build_inspection,
    build_partition,
    build_request,
)

MARKUP_LABEL = "<b>STICK</b>"


def candidate_for(inspection: DriveInspection) -> TargetCandidate:
    """Return a picker row for one inspected drive."""
    return TargetCandidate(name=inspection.drive.name, inspection=inspection, error=None)


def open_dialog(
    qtbot: Any,
    candidates: tuple[TargetCandidate, ...],
    request: PreflightRequest | None = None,
) -> UsbExportDialog:
    """Open the export dialog over a fixed drive list."""
    dialog = UsbExportDialog(lambda: candidates, request or build_request())
    qtbot.addWidget(dialog)
    return dialog


def choose_first(dialog: UsbExportDialog) -> None:
    """Pick the first drive and move to the Confirm step."""
    dialog.drive_list.setCurrentRow(0)
    dialog.next_btn.click()


def list_texts(widget: QListWidget) -> list[str]:
    """Return the text of every row in a list widget."""
    return [widget.item(row).text() for row in range(widget.count())]


def test_opens_on_choose_drive(qtbot: Any) -> None:
    """The dialog starts on Choose drive with Next off until a drive is picked."""
    dialog = open_dialog(qtbot, (candidate_for(build_inspection()),))

    assert dialog.pages.currentIndex() == PAGE_CHOOSE
    assert dialog.status_label.text() == CHOOSE_PROMPT_TEXT
    assert not dialog.next_btn.isEnabled()
    dialog.drive_list.setCurrentRow(0)
    assert dialog.next_btn.isEnabled()


def test_empty_and_failed_drive_lists(qtbot: Any) -> None:
    """No drives and a failed listing each say so, and Next stays off."""

    def fail() -> tuple[TargetCandidate, ...]:
        raise DriveInspectionError("lsblk: raw output")

    empty = open_dialog(qtbot, ())
    failed = UsbExportDialog(fail, build_request())
    qtbot.addWidget(failed)

    assert empty.status_label.text() == NO_DRIVES_TEXT
    assert failed.status_label.text() == LOAD_FAILED_TEXT
    assert not empty.next_btn.isEnabled()
    assert not failed.next_btn.isEnabled()


def test_next_shows_drive_back_returns(qtbot: Any) -> None:
    """Next shows the chosen drive. Back returns with the same drive picked."""
    dialog = open_dialog(qtbot, (candidate_for(build_inspection()),))

    choose_first(dialog)

    assert dialog.pages.currentIndex() == PAGE_CONFIRM
    assert dialog.drive_name_label.text() == "STICK"
    dialog.back_btn.click()
    assert dialog.pages.currentIndex() == PAGE_CHOOSE
    assert dialog.drive_list.currentRow() == 0


def test_details_start_closed(qtbot: Any) -> None:
    """The identifiers sit behind a closed Drive details toggle."""
    dialog = open_dialog(qtbot, (candidate_for(build_inspection()),))
    choose_first(dialog)

    assert not dialog.details_section.is_expanded()
    assert "Serial number" in dialog.details_label.text()
    assert "1A2B-3C4D" in dialog.details_label.text()


def test_space_bar_and_summary(qtbot: Any) -> None:
    """The space line and bar show the needed share of the free space."""
    inspection = build_inspection()
    dialog = open_dialog(qtbot, (candidate_for(inspection),))
    result = evaluate_preflight(inspection, build_request())

    choose_first(dialog)

    assert dialog.space_label.text() == space_summary(result)
    assert dialog.space_bar.maximum() == SPACE_BAR_STEPS
    assert dialog.space_bar.value() == space_bar_value(needed_bytes(result), FREE_BYTES)
    assert not dialog.space_bar.isHidden()


def test_drive_changes_or_empty_sentence(qtbot: Any) -> None:
    """Confirm lists what happens on the drive, or says nothing changes."""
    candidates = (candidate_for(build_inspection()),)
    listed = open_dialog(qtbot, candidates)
    empty = open_dialog(qtbot, candidates, build_request(planned_changes=()))

    choose_first(listed)
    choose_first(empty)

    assert list_texts(listed.changes_list) == ["Add 2 tracks", "Update 1 playlist"]
    assert list_texts(empty.changes_list) == [NO_DRIVE_CHANGES_TEXT]


def test_export_accepts_a_passing_drive(qtbot: Any) -> None:
    """Export turns on for a passing drive, accepts, and returns that drive."""
    inspection = build_inspection()
    dialog = open_dialog(qtbot, (candidate_for(inspection),))
    choose_first(dialog)

    assert dialog.export_btn.isEnabled()
    assert dialog.cancel_btn.isDefault()
    dialog.export_btn.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_inspection() == inspection


def test_refusal_disables_export_with_reason(qtbot: Any) -> None:
    """A refused drive shows the reason and its guidance. Export stays off."""
    partition = build_partition(filesystem_health=FilesystemHealth.ERRORS)
    inspection = build_inspection(partitions=(partition,))
    refusal = evaluate_preflight(inspection, build_request()).refusals[0]
    dialog = open_dialog(qtbot, (candidate_for(inspection),))

    choose_first(dialog)

    assert not dialog.export_btn.isEnabled()
    assert list_texts(dialog.refusals_list) == [f"{refusal.message} {refusal.guidance}".strip()]
    assert not dialog.refusals_section.isHidden()
    assert not dialog.drive_group.isHidden()


def test_safety_refusal_hides_drive_space(qtbot: Any) -> None:
    """A drive that must never take an export shows no identity and no space."""
    inspection = build_inspection(drive=build_drive(is_system=True))
    dialog = UsbExportDialog(lambda: (candidate_for(inspection),), build_request())
    qtbot.addWidget(dialog)

    dialog.show_confirm(inspection)

    assert not dialog.export_btn.isEnabled()
    assert dialog.drive_group.isHidden()
    assert dialog.space_group.isHidden()
    assert dialog.refusals_list.count() == 1


def test_drive_text_stays_plain(qtbot: Any) -> None:
    """A drive name with markup shows as plain text."""
    inspection = build_inspection(partitions=(build_partition(label=MARKUP_LABEL),))
    dialog = open_dialog(qtbot, (candidate_for(inspection),))
    choose_first(dialog)

    assert dialog.drive_name_label.textFormat() == Qt.TextFormat.PlainText
    assert dialog.drive_name_label.text() == MARKUP_LABEL
    assert dialog.details_label.textFormat() == Qt.TextFormat.PlainText
