"""Tests for the notice callback of the USB recovery launch scan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from infiniteprep.config.settings import SettingsManager
from infiniteprep.main import process_usb_recovery_at_launch
from infiniteprep.services.usb.manifest import ExportScope
from infiniteprep.services.usb.recovery import UsbJournalEntry, UsbRecoveryJournalStore
from tests.fixtures.call_recorder import CallRecorder
from tests.unit.usb_preflight_support import build_drive

STARTED_AT_UTC = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CLOCK_BEHIND = timedelta(days=400)


def _build_store(root: Path) -> UsbRecoveryJournalStore:
    """Return a journal store that holds one unresolved entry."""
    store = UsbRecoveryJournalStore(root / "usb_recovery_journal.json")
    store.save_entries(
        [
            UsbJournalEntry(
                export_id="exp-notice",
                started_at_utc="2026-10-01T12:00:00Z",
                volume_uuid="1A2B-3C4D",
                drive_identity=build_drive(),
                mount_point="/media/dj/STICK",
                scope=ExportScope.SINGLE_PLAYLIST,
                playlist_names=("Club Set",),
                track_paths=("crate/a.aiff",),
            )
        ]
    )
    return store


def test_empty_recorder_gets_the_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A passed callback receives the notice even when it is falsy, such as an empty list."""
    dialog_calls = CallRecorder()
    monkeypatch.setattr("infiniteprep.main._show_usb_recovery_notice", dialog_calls)
    notices = CallRecorder()

    process_usb_recovery_at_launch(
        settings_manager=SettingsManager(tmp_path / "settings.json"),
        journal_store=_build_store(tmp_path),
        now_utc=STARTED_AT_UTC - CLOCK_BEHIND,
        show_notice=notices,
    )

    assert [title for title, _body in notices] == ["USB Recovery Clock Check"]
    assert dialog_calls == []
