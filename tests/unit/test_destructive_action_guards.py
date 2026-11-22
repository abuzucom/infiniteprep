"""Behavioral tests for the guards in front of destructive or irreversible UI actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from infiniteprep.config.settings import AppSettings
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.usb.manifest import ExportScope
from infiniteprep.services.usb.preflight import evaluate_preflight
from infiniteprep.services.usb.recovery import (
    ReconnectChoice,
    UsbJournalEntry,
    UsbReconnectMatch,
    UsbRecoveryJournalStore,
)
from infiniteprep.services.usb.targets import TargetCandidate
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.settings import storage as storage_module
from infiniteprep.ui.settings.storage import StorageTab
from infiniteprep.ui.usb_preflight_dialog import UsbPreflightDialog
from infiniteprep.ui.usb_recovery_dialog import UsbRecoveryDialog
from infiniteprep.ui.write_plan_dialog import NO_SAFE_PLANS_TEXT, WritePlanDialog
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.qt_dialogs import MESSAGE_ICON_INDEX, MESSAGE_TEXT_INDEX, QUESTION_TEXT_INDEX
from tests.unit.usb_preflight_support import (
    build_drive,
    build_inspection,
    build_partition,
    build_request,
)

EXPORT_ID = "exp-guard"
MOUNT_POINT = "D:\\USB"


def show_active(qtbot: Any, widget: QWidget) -> None:
    """Show a top-level widget and wait until it is the active window."""
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    widget.activateWindow()
    qtbot.waitUntil(widget.isActiveWindow)


def press_enter(qtbot: Any, dialog: QDialog) -> None:
    """Press Enter on the widget that holds focus inside the dialog."""
    target = QApplication.focusWidget() or dialog
    qtbot.keyClick(target, Qt.Key.Key_Return)


def build_entry() -> UsbJournalEntry:
    """Return one unresolved USB recovery journal entry."""
    return UsbJournalEntry(
        export_id=EXPORT_ID,
        started_at_utc="2026-11-15T12:00:00Z",
        volume_uuid="1A2B-3C4D",
        drive_identity=build_drive(),
        mount_point=MOUNT_POINT,
        scope=ExportScope.SINGLE_PLAYLIST,
        playlist_names=("Warmup",),
        track_paths=("crate/track.aiff",),
        backup_path="backups/exp-guard",
    )


def build_match() -> UsbReconnectMatch:
    """Return an exact reconnect match, so Resume and Restore start enabled."""
    drive = build_drive()
    partition = build_partition(uuid="1A2B-3C4D", mount_points=(MOUNT_POINT,))
    inspection = build_inspection(drive=drive, partitions=(partition,))
    candidate = TargetCandidate(name=drive.name, inspection=inspection, error=None)
    return UsbReconnectMatch(
        entry=build_entry(),
        candidate=candidate,
        requires_confirmation=False,
        reason="Exact match.",
    )


@pytest.fixture
def journal_store(tmp_path: Path) -> UsbRecoveryJournalStore:
    """Return a journal store in tmp_path that holds one unresolved entry."""
    store = UsbRecoveryJournalStore(tmp_path / "usb_recovery_journal.json", allowed_root=tmp_path)
    store.save_entries([build_entry()])
    return store


def test_enter_in_usb_preflight_cancels_the_export(qtbot: Any) -> None:
    """Enter in the USB confirm dialog cancels. Only an explicit choice confirms."""
    inspection = build_inspection()
    request = build_request()
    dialog = UsbPreflightDialog(inspection, request, evaluate_preflight(inspection, request))
    show_active(qtbot, dialog)
    assert dialog.confirm_btn.isEnabled()

    press_enter(qtbot, dialog)

    assert dialog.result() == QDialog.DialogCode.Rejected


def test_usb_recovery_enter_keeps_drive(qtbot: Any) -> None:
    """Enter in the reconnect prompt picks Leave Unchanged, never Resume or Restore."""
    dialog = UsbRecoveryDialog(build_match())
    show_active(qtbot, dialog)
    assert dialog.resume_btn.isEnabled()

    press_enter(qtbot, dialog)

    assert dialog.selected_choice is ReconnectChoice.LEAVE_UNCHANGED


def test_declined_journal_cleanup_keeps_every_entry(
    qtbot: Any, journal_store: UsbRecoveryJournalStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No to the cleanup question removes nothing and reports nothing."""
    # The question recorder keeps each question text and answers No.
    questions = CallRecorder(QUESTION_TEXT_INDEX, reply=False)
    messages = CallRecorder(MESSAGE_TEXT_INDEX)
    monkeypatch.setattr(storage_module, "ask_plain_question", questions)
    monkeypatch.setattr(storage_module, "show_plain_message", messages)
    tab = StorageTab(AppSettings(), journal_store=journal_store)
    qtbot.addWidget(tab)

    tab.usb_recovery_cleanup_btn.click()

    assert len(questions) == 1
    assert messages == []
    assert [entry.export_id for entry in journal_store.load_entries()] == [EXPORT_ID]


def test_journal_cleanup_reports_count(
    qtbot: Any, journal_store: UsbRecoveryJournalStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yes to the cleanup question removes the entries and reports the count in a message."""
    messages = CallRecorder(MESSAGE_ICON_INDEX, MESSAGE_TEXT_INDEX)
    monkeypatch.setattr(storage_module, "ask_plain_question", CallRecorder(reply=True))
    monkeypatch.setattr(storage_module, "show_plain_message", messages)
    tab = StorageTab(AppSettings(), journal_store=journal_store)
    qtbot.addWidget(tab)

    tab.usb_recovery_cleanup_btn.click()

    assert journal_store.load_entries() == []
    assert len(messages) == 1
    assert messages[0][0] == QMessageBox.Icon.Information
    assert "1" in messages[0][1]


def test_no_safe_plans_keeps_preview_open(qtbot: Any) -> None:
    """Apply with no safe plan says so and leaves the preview open."""
    dialog = WritePlanDialog(WriteService(Repository(DatabaseManager(":memory:"))), [])
    qtbot.addWidget(dialog)

    dialog.execute_safe_writes()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.status_label.text() == NO_SAFE_PLANS_TEXT
