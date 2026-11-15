"""Qt behavioral tests for UsbRecoveryDialog and StorageTab USB recovery settings."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
WORKTREE_SRC = WORKTREE_ROOT / "src"
for _candidate in (str(WORKTREE_ROOT), str(WORKTREE_SRC)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from infiniteprep.config.credentials import CredentialStore  # noqa: E402
from infiniteprep.config.settings import AppSettings, SettingsManager  # noqa: E402
from infiniteprep.services.usb.manifest import ExportScope  # noqa: E402
from infiniteprep.services.usb.recovery import (  # noqa: E402
    DEFAULT_RECOVERY_EXPIRY_DAYS,
    ReconnectChoice,
    UsbJournalEntry,
    UsbReconnectMatch,
)
from infiniteprep.services.usb.targets import TargetCandidate  # noqa: E402
from infiniteprep.ui.settings.storage import StorageTab  # noqa: E402
from infiniteprep.ui.settings_dialog import SettingsDialog  # noqa: E402
from infiniteprep.ui.usb_recovery_dialog import (  # noqa: E402
    UsbRecoveryDialog,
    format_expiry_warning_text,
    format_overdue_cleanup_text,
)
from tests.unit.usb_preflight_support import (  # noqa: E402
    build_drive,
    build_inspection,
    build_partition,
)


def _ensure_qapp() -> QApplication:
    """Return an active QApplication instance for widget tests."""
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


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
    return UsbReconnectMatch(
        entry=entry,
        candidate=candidate,
        requires_confirmation=requires_confirmation,
        reason="Mount path changed since export started." if requires_confirmation else "Exact match.",
    )


class UsbRecoveryDialogTests(unittest.TestCase):
    """Verify reconnect choices, confirmation gating, and plain-text rendering."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = _ensure_qapp()

    def test_drive_fields_render_as_plain_text(self) -> None:
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

    def test_reconnect_choice_buttons_set_selected_choice_and_accept(self) -> None:
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

    def test_confirmation_gates_resume_and_restore_until_checked(self) -> None:
        dialog = UsbRecoveryDialog(_build_match(requires_confirmation=True))
        self.addCleanup(dialog.close)

        self.assertIsNotNone(dialog.confirm_checkbox)
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
        entry = _build_match(requires_confirmation=False).entry
        warning = format_expiry_warning_text([entry])
        overdue = format_overdue_cleanup_text([entry])

        self.assertIn("1 day", warning.casefold())
        self.assertIn(entry.export_id, warning)
        self.assertIn("expired", overdue.casefold())
        self.assertIn(entry.export_id, overdue)


class StorageTabUsbRecoverySettingsTests(unittest.TestCase):
    """Verify StorageTab and SettingsDialog persist USB recovery expiry preferences."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = _ensure_qapp()

    def test_storage_tab_exposes_controls_and_persists_settings(self) -> None:
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

    def test_default_settings_and_validation_for_usb_recovery_expiry(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
