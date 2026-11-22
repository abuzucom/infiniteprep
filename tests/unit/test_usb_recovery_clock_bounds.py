"""Tests for the host clock bounds of the USB recovery expiry scan.

A wrong host clock must never remove a recovery entry. The scan keeps an entry whose
age is negative or beyond the plausible ceiling, and reports it instead.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from infiniteprep.config.settings import SettingsManager
from infiniteprep.main import process_usb_recovery_at_launch
from infiniteprep.services.usb.manifest import ExportScope
from infiniteprep.services.usb.recovery import (
    MAX_RECOVERY_EXPIRY_DAYS,
    UsbExpiryScanResult,
    UsbJournalEntry,
    UsbRecoveryJournalStore,
)
from infiniteprep.ui.usb_recovery_dialog import format_clock_suspect_text
from tests.fixtures.call_recorder import CallRecorder
from tests.unit.usb_preflight_support import build_drive

STARTED_AT_TEXT = "2026-10-01T12:00:00Z"
STARTED_AT_UTC = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
EXPIRY_DAYS = 30
PLAUSIBLE_AGE_CEILING = timedelta(days=EXPIRY_DAYS + MAX_RECOVERY_EXPIRY_DAYS)
ONE_SECOND = timedelta(seconds=1)
CLOCK_FAR_BEHIND = timedelta(days=400)
ENTRY_ID = "exp-clock"


def _build_entry() -> UsbJournalEntry:
    """Build an unresolved journal entry that started at STARTED_AT_UTC."""
    return UsbJournalEntry(
        export_id=ENTRY_ID,
        started_at_utc=STARTED_AT_TEXT,
        volume_uuid="1A2B-3C4D",
        drive_identity=build_drive(),
        mount_point="/media/dj/STICK",
        scope=ExportScope.SINGLE_PLAYLIST,
        playlist_names=("Club Set",),
        track_paths=("crate/a.aiff",),
    )


def _stored_ids(store: UsbRecoveryJournalStore) -> list[str]:
    """Return the export ids the journal still holds."""
    return [item.export_id for item in store.load_entries()]


def _suspect_ids(scan: UsbExpiryScanResult) -> list[str]:
    """Return the export ids the scan reported as clock suspect."""
    return [item.export_id for item in scan.clock_suspect_entries]


class UsbRecoveryClockBoundsTests(unittest.TestCase):
    """Verify that an implausible host clock removes no recovery entry."""

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.store = UsbRecoveryJournalStore(self.root / "usb_recovery_journal.json")
        self.store.save_entries([_build_entry()])

    def test_clock_behind_entry_keeps_and_reports(self) -> None:
        """A clock before the start time gives a negative age. The entry stays."""
        scan = self.store.evaluate_expiry(STARTED_AT_UTC - ONE_SECOND, at_launch=True)

        self.assertEqual(_suspect_ids(scan), [ENTRY_ID])
        self.assertEqual(scan.warning_entries, ())
        self.assertEqual(scan.expired_deleted_entries, ())
        self.assertEqual(scan.overdue_notice_entries, ())
        self.assertEqual(_stored_ids(self.store), [ENTRY_ID])
        self.assertFalse(self.store.load_entries()[0].warned_expiry)

    def test_clock_far_ahead_keeps_and_reports_the_entry(self) -> None:
        """An age beyond the ceiling marks the clock as suspect. No cleanup runs."""
        far_future = STARTED_AT_UTC + PLAUSIBLE_AGE_CEILING + ONE_SECOND

        for at_launch in (True, False):
            with self.subTest(at_launch=at_launch):
                scan = self.store.evaluate_expiry(
                    far_future, expiry_days=EXPIRY_DAYS, at_launch=at_launch
                )

                self.assertEqual(_suspect_ids(scan), [ENTRY_ID])
                self.assertEqual(scan.expired_deleted_entries, ())
                self.assertEqual(scan.overdue_notice_entries, ())
                self.assertEqual(_stored_ids(self.store), [ENTRY_ID])

    def test_age_at_the_ceiling_still_expires(self) -> None:
        """The ceiling itself counts as plausible. The launch scan removes the entry."""
        scan = self.store.evaluate_expiry(
            STARTED_AT_UTC + PLAUSIBLE_AGE_CEILING, expiry_days=EXPIRY_DAYS, at_launch=True
        )

        self.assertEqual(scan.clock_suspect_entries, ())
        self.assertEqual([item.export_id for item in scan.overdue_notice_entries], [ENTRY_ID])
        self.assertEqual(_stored_ids(self.store), [])

    def test_scan_result_keeps_its_three_field_form(self) -> None:
        """Existing three-argument construction still works and reports no suspect entry."""
        self.assertEqual(UsbExpiryScanResult((), (), ()).clock_suspect_entries, ())

    def test_launch_shows_one_notice_for_suspect_entries(self) -> None:
        """The launch scan shows a clock notice that names the kept entry."""
        settings_manager = SettingsManager(self.root / "settings.json")
        settings_manager.update(
            usb_recovery_expiry_enabled=True, usb_recovery_expiry_days=EXPIRY_DAYS
        )
        notices = CallRecorder()

        scan = process_usb_recovery_at_launch(
            settings_manager=settings_manager,
            journal_store=self.store,
            now_utc=STARTED_AT_UTC - CLOCK_FAR_BEHIND,
            show_notice=notices,
        )

        self.assertEqual(_suspect_ids(scan), [ENTRY_ID])
        expected_body = format_clock_suspect_text(scan.clock_suspect_entries)
        self.assertEqual(notices, [("USB Recovery Clock Check", expected_body)])
        self.assertIn(ENTRY_ID, expected_body)
        self.assertEqual(_stored_ids(self.store), [ENTRY_ID])


if __name__ == "__main__":
    unittest.main()
