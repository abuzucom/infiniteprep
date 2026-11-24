"""Qt behavioral tests for UsbRecoveryDialog and StorageTab USB recovery settings."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel

from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.main import process_usb_recovery_at_launch
from infiniteprep.services.usb.manifest import ExportScope
from infiniteprep.services.usb.recovery import (
    DEFAULT_RECOVERY_EXPIRY_DAYS,
    ReconnectChoice,
    UsbExpiryScanResult,
    UsbJournalEntry,
    UsbReconnectMatch,
    UsbRecoveryJournalStore,
)
from infiniteprep.services.usb.targets import TargetCandidate
from infiniteprep.ui.settings.storage import RECOVERY_READ_ERROR_TITLE, StorageTab
from infiniteprep.ui.settings_dialog import SettingsDialog
from infiniteprep.ui.usb_recovery_dialog import (
    UsbRecoveryDialog,
    format_expiry_warning_text,
    format_overdue_cleanup_text,
)
from tests.fixtures.call_recorder import CallRecorder
from tests.unit.usb_preflight_support import (
    build_drive,
    build_inspection,
    build_partition,
)

CORRUPT_LAUNCH_CHECK_UTC = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def _build_match(*, requires_confirmation: bool, html_text: bool = False) -> UsbReconnectMatch:
    """Construct a UsbReconnectMatch fixture."""
    label = "<b>STICK</b>" if html_text else "STICK"
    model = "<i>Ultra</i>" if html_text else "Ultra USB 3.0"
    vendor = "<u>SanDisk</u>" if html_text else "SanDisk"
    drive = build_drive(model=model, vendor=vendor, serial="SER<123>")
    partition = build_partition(
        label=label, uuid="1A2B-<3C4D>" if html_text else "1A2B-3C4D", mount_points=("D:\\USB",)
    )
    inspection = build_inspection(drive=drive, partitions=(partition,))
    entry = UsbJournalEntry(
        export_id="exp-100",
        started_at_utc="2026-11-15T12:00:00Z",
        volume_uuid=partition.uuid or "",
        drive_identity=drive,
        mount_point="D:\\USB",
        scope=ExportScope.SINGLE_PLAYLIST,
        playlist_names=("Warmup",),
        track_paths=("crate/track.aiff",),
        backup_path="backups/exp-100",
    )
    candidate = TargetCandidate(name=drive.name, inspection=inspection, error=None)
    reason = "Mount path changed since export started." if requires_confirmation else "Exact match."
    return UsbReconnectMatch(
        entry=entry,
        candidate=candidate,
        requires_confirmation=requires_confirmation,
        reason=reason,
    )


@pytest.mark.usefixtures("qapp")
class UsbRecoveryDialogTests(unittest.TestCase):
    """Verify reconnect choices, confirmation gating, and plain-text rendering."""

    def test_drive_fields_render_as_plain_text(self) -> None:
        """Every dialog label uses plain text and shows markup in drive fields as typed."""
        dialog = UsbRecoveryDialog(_build_match(requires_confirmation=False, html_text=True))
        self.addCleanup(dialog.close)

        plain_texts: list[str] = []
        for label in dialog.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
            plain_texts.append(label.text())

        joined = "\n".join(plain_texts)
        self.assertIn("<b>STICK</b>", joined)
        self.assertIn("<i>Ultra</i>", joined)
        self.assertIn("<u>SanDisk</u>", joined)
        self.assertIn("SER<123>", joined)

    def test_reconnect_buttons_pick_and_accept(self) -> None:
        """Each choice button records its choice and accepts the dialog."""
        cases = (
            ("resume_btn", ReconnectChoice.RESUME),
            ("restore_btn", ReconnectChoice.RESTORE),
            ("leave_unchanged_btn", ReconnectChoice.LEAVE_UNCHANGED),
        )
        for attr_name, expected_choice in cases:
            with self.subTest(choice=expected_choice):
                dialog = UsbRecoveryDialog(_build_match(requires_confirmation=False))
                self.addCleanup(dialog.close)
                getattr(dialog, attr_name).click()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertEqual(dialog.selected_choice, expected_choice)

    def test_resume_and_restore_need_confirmation(self) -> None:
        """Resume and Restore stay disabled while the confirmation box is clear."""
        dialog = UsbRecoveryDialog(_build_match(requires_confirmation=True))
        self.addCleanup(dialog.close)

        assert dialog.confirm_checkbox is not None
        self.assertFalse(dialog.confirm_checkbox.isChecked())
        self.assertFalse(dialog.resume_btn.isEnabled())
        self.assertFalse(dialog.restore_btn.isEnabled())
        self.assertTrue(dialog.leave_unchanged_btn.isEnabled())

        dialog.confirm_checkbox.setChecked(True)
        self.assertTrue(dialog.resume_btn.isEnabled())
        self.assertTrue(dialog.restore_btn.isEnabled())

        dialog.confirm_checkbox.setChecked(False)
        self.assertFalse(dialog.resume_btn.isEnabled())
        self.assertFalse(dialog.restore_btn.isEnabled())

    def test_notice_formatters_render_entry_details(self) -> None:
        """The warning and overdue notices name the export and the expiry period."""
        entry = _build_match(requires_confirmation=False).entry
        warning = format_expiry_warning_text([entry])
        custom_warning = format_expiry_warning_text([entry], expiry_days=14)
        overdue = format_overdue_cleanup_text([entry])

        self.assertIn("1 day", warning.casefold())
        self.assertIn("30 days", warning.casefold())
        self.assertIn("14 days", custom_warning.casefold())
        self.assertIn(entry.export_id, warning)
        self.assertIn("expired", overdue.casefold())
        self.assertIn(entry.export_id, overdue)

    def test_launch_runs_usb_expiry_with_notices(self) -> None:
        """Launch deletes the overdue entry, warns about the aging one, and keeps the fresh one."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings_mgr = SettingsManager(root / "settings.json")
            settings_mgr.update(usb_recovery_expiry_enabled=True, usb_recovery_expiry_days=30)
            store = UsbRecoveryJournalStore(root / "usb_recovery_journal.json")

            base_entry = _build_match(requires_confirmation=False).entry
            warn_entry = UsbJournalEntry(
                export_id="exp-warn",
                started_at_utc="2026-10-02T12:00:00Z",
                volume_uuid=base_entry.volume_uuid,
                drive_identity=base_entry.drive_identity,
                mount_point=base_entry.mount_point,
                scope=base_entry.scope,
                playlist_names=base_entry.playlist_names,
                track_paths=base_entry.track_paths,
            )
            overdue_entry = UsbJournalEntry(
                export_id="exp-overdue",
                started_at_utc="2026-09-29T12:00:00Z",
                volume_uuid=base_entry.volume_uuid,
                drive_identity=base_entry.drive_identity,
                mount_point=base_entry.mount_point,
                scope=base_entry.scope,
                playlist_names=base_entry.playlist_names,
                track_paths=base_entry.track_paths,
            )
            # Exactly 28 days old at now_utc: one day short of the 29-day warning point.
            fresh_entry = replace(
                warn_entry, export_id="exp-fresh", started_at_utc="2026-10-03T13:00:00Z"
            )
            store.save_entries([warn_entry, overdue_entry, fresh_entry])

            notices = CallRecorder()
            now_utc = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
            scan = process_usb_recovery_at_launch(
                settings_manager=settings_mgr,
                journal_store=store,
                now_utc=now_utc,
                show_notice=notices,
            )

            self.assertEqual(
                [item.export_id for item in scan.overdue_notice_entries],
                ["exp-overdue"],
            )
            self.assertEqual([item.export_id for item in scan.warning_entries], ["exp-warn"])
            self.assertEqual(len(notices), 2)
            self.assertIn("exp-overdue", notices[0][1])
            self.assertIn("30 days", notices[1][1])
            self.assertIn("exp-warn", notices[1][1])
            remaining = {item.export_id: item for item in store.load_entries()}
            self.assertEqual(sorted(remaining), ["exp-fresh", "exp-warn"])
            self.assertTrue(remaining["exp-warn"].warned_expiry)
            # The classifier keeps the younger entry without a warning.
            self.assertFalse(remaining["exp-fresh"].warned_expiry)


@pytest.mark.usefixtures("qapp")
class StorageTabUsbRecoverySettingsTests(unittest.TestCase):
    """Verify StorageTab and SettingsDialog persist USB recovery expiry preferences."""

    def test_storage_tab_controls_save_settings(self) -> None:
        """The Storage tab shows the expiry defaults and saves changed values."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mgr = SettingsManager(Path(tmp_dir) / "settings.json")
            creds = CredentialStore(use_memory_backend=True)
            dialog = SettingsDialog(mgr, creds)
            self.addCleanup(dialog.close)

            tab = dialog.storage_tab
            self.assertIsInstance(tab, StorageTab)
            self.assertTrue(tab.usb_recovery_expiry_checkbox.isChecked())
            self.assertEqual(tab.usb_recovery_expiry_days_spin.value(), 30)
            self.assertTrue(tab.usb_recovery_cleanup_btn.isEnabled())

            tab.usb_recovery_expiry_checkbox.setChecked(False)
            tab.usb_recovery_expiry_days_spin.setValue(45)
            dialog.save_settings()

            reloaded: AppSettings = mgr.load()
            self.assertFalse(reloaded.usb_recovery_expiry_enabled)
            self.assertEqual(reloaded.usb_recovery_expiry_days, 45)

    def test_usb_expiry_defaults_and_validation(self) -> None:
        """Expiry defaults to on at 30 days and rejects day counts outside 1-365."""
        defaults = AppSettings()
        self.assertTrue(defaults.usb_recovery_expiry_enabled)
        self.assertEqual(defaults.usb_recovery_expiry_days, DEFAULT_RECOVERY_EXPIRY_DAYS)
        self.assertEqual(defaults.usb_recovery_expiry_days, 30)

        with tempfile.TemporaryDirectory() as tmp_dir:
            mgr = SettingsManager(Path(tmp_dir) / "settings.json")
            with self.assertRaises(ValueError):
                mgr.update(usb_recovery_expiry_days=0)
            with self.assertRaises(ValueError):
                mgr.update(usb_recovery_expiry_days=366)
            saved = mgr.update(usb_recovery_expiry_enabled=False, usb_recovery_expiry_days=15)
            self.assertFalse(saved.usb_recovery_expiry_enabled)
            self.assertEqual(saved.usb_recovery_expiry_days, 15)

    def test_corrupt_journal_is_kept_and_reported(self) -> None:
        """Launch and manual cleanup each report one error and leave a corrupt journal intact."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings_mgr = SettingsManager(root / "settings.json")
            journal_path = root / "usb_recovery_journal.json"
            corrupt_text = "{corrupt-json"
            journal_path.write_text(corrupt_text, encoding="utf-8")
            store = UsbRecoveryJournalStore(journal_path, allowed_root=root)

            launch_notices = CallRecorder()
            scan = process_usb_recovery_at_launch(
                settings_manager=settings_mgr,
                journal_store=store,
                now_utc=CORRUPT_LAUNCH_CHECK_UTC,
                show_notice=launch_notices,
            )
            self.assertEqual(scan, UsbExpiryScanResult((), (), ()))
            self.assertEqual(len(launch_notices), 1)
            self.assertIn("Error", launch_notices[0][0])
            self.assertEqual(journal_path.read_text(encoding="utf-8"), corrupt_text)

            tab = StorageTab(AppSettings())
            self.addCleanup(tab.close)
            cleanup_errors = CallRecorder()
            removed_count = tab.clean_usb_recovery_journal(
                journal_store=store,
                show_error=cleanup_errors,
            )
            self.assertIsNone(removed_count)
            self.assertEqual(len(cleanup_errors), 1)
            self.assertEqual(cleanup_errors[0][0], RECOVERY_READ_ERROR_TITLE)
            self.assertEqual(journal_path.read_text(encoding="utf-8"), corrupt_text)


if __name__ == "__main__":
    unittest.main()
