"""Behavioral tests for announced status changes and feedback on no-op toolbar actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui import accessible_names as accessible_names_module
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.audio_player import AudioPreviewWidget
from infiniteprep.ui.batch_queue import format_batch_row
from infiniteprep.ui.detail_panel import DetailPanel
from infiniteprep.ui.main_window import (
    NO_VISIBLE_FILES_TEXT,
    TABLE_COLUMNS,
    MainWindow,
    format_status_cell,
)
from infiniteprep.ui.write_plan_dialog import WritePlanDialog
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.memory_settings import MemorySettingsManager
from tests.fixtures.qt_dialogs import MESSAGE_ICON_INDEX, MESSAGE_TEXT_INDEX, MESSAGE_TITLE_INDEX
from tests.fixtures.waveform_stub import keep_waveform_pool_idle

STATUS_COLUMN: int = TABLE_COLUMNS.index("Status")
PLAYBACK_ERROR: str = "Playback failed: decoder error."
BATCH_LABEL: str = "Announce check"
BATCH_TIMEOUT_MS: int = 10_000


@pytest.fixture
def announcements(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every announcement message instead of sending it to the platform."""
    spoken: list[str] = []

    def record_announcement(event: Any) -> None:
        """Keep the message text of one accessibility announcement."""
        spoken.append(event.message())

    monkeypatch.setattr(
        accessible_names_module.QAccessible, "updateAccessibility", record_announcement
    )
    return spoken


@pytest.fixture
def messages(monkeypatch: pytest.MonkeyPatch) -> list[tuple[QMessageBox.Icon, str, str]]:
    """Record main window messages instead of opening a blocking box."""
    shown = CallRecorder(MESSAGE_ICON_INDEX, MESSAGE_TITLE_INDEX, MESSAGE_TEXT_INDEX)
    monkeypatch.setattr(main_window_module, "SettingsManager", MemorySettingsManager)
    monkeypatch.setattr(main_window_module, "show_plain_message", shown)
    return shown


def build_window(qtbot: Any, tmp_path: Path, *, with_track: bool, **callbacks: Any) -> MainWindow:
    """Open a main window over an empty library or a library of one WAV."""
    repository = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repository, MetadataTagAdapter())
    if with_track:
        importer.import_paths([create_wav_fixture(tmp_path / "crate" / "a.wav")])
    window = MainWindow(
        repository=repository,
        import_service=importer,
        review_service=ReviewService(repository),
        **callbacks,
    )
    qtbot.addWidget(window)
    return window


def test_playback_status_is_announced(qtbot: Any, announcements: list[str]) -> None:
    """A playback status message reaches a screen reader. Clearing it says nothing."""
    player = AudioPreviewWidget()
    qtbot.addWidget(player)

    player.show_status(PLAYBACK_ERROR)
    player.show_status("")

    assert announcements == [PLAYBACK_ERROR]


def test_analysis_failure_is_announced(qtbot: Any, announcements: list[str]) -> None:
    """A spectrum analysis failure reaches a screen reader with its reason."""
    player = AudioPreviewWidget()
    qtbot.addWidget(player)

    player.show_analysis_failure("file is unreadable")

    assert announcements == ["Couldn't draw the waveform: file is unreadable"]


def test_write_preview_count_is_announced(qtbot: Any, announcements: list[str]) -> None:
    """The write preview announces its ready count and the reason Apply did nothing."""
    dialog = WritePlanDialog(WriteService(Repository(DatabaseManager(":memory:"))), [])
    qtbot.addWidget(dialog)

    dialog.execute_safe_writes()

    assert announcements[0] == "0 of 0 files ready for safe backed-up write."
    assert announcements[-1] == dialog.status_label.text()


def test_toolbar_actions_explain_an_empty_library(
    qtbot: Any, tmp_path: Path, messages: list[tuple[QMessageBox.Icon, str, str]]
) -> None:
    """Every toolbar action on the visible files says why it did nothing on an empty table."""
    # The recorder keeps the two export callback arguments as one pair.
    calls = CallRecorder()
    window = build_window(
        qtbot,
        tmp_path,
        with_track=False,
        on_analyze_batch=calls.append,
        on_open_write_dialog=calls.append,
        on_transcode_batch=calls.append,
        on_export_playlist=calls,
    )
    # Identify & Analyze and Preview & Apply Writes keep no button attribute, so their
    # handlers run directly. The other three run through their buttons.
    actions = (
        window._trigger_analyze_all,
        window._trigger_open_write_dialog,
        window.bulk_btn.click,
        window.transcode_btn.click,
        window.export_playlist_btn.click,
    )
    for action in actions:
        action()

    assert calls == []
    assert [text for _icon, _title, text in messages] == [NO_VISIBLE_FILES_TEXT] * len(actions)


def test_finished_batch_is_announced(
    qtbot: Any,
    tmp_path: Path,
    messages: list[tuple[QMessageBox.Icon, str, str]],
    announcements: list[str],
) -> None:
    """A batch that finishes in the queue reaches a screen reader with its result row."""
    window = build_window(qtbot, tmp_path, with_track=False)

    with qtbot.waitSignal(window.batch_queue.idle, timeout=BATCH_TIMEOUT_MS):
        batch = window.queue_batch(BatchKind.ANALYZE, BATCH_LABEL, lambda _cancel, _progress: None)

    assert announcements[-1] == format_batch_row(batch)
    assert announcements[-1].startswith(BATCH_LABEL)


def test_bulk_approve_reports_the_approved_count(
    qtbot: Any, tmp_path: Path, messages: list[tuple[QMessageBox.Icon, str, str]]
) -> None:
    """Bulk Approve on visible files reports how many fields it approved."""
    window = build_window(qtbot, tmp_path, with_track=True)

    window.bulk_btn.click()

    assert len(messages) == 1
    icon, _title, text = messages[0]
    assert icon == QMessageBox.Icon.Information
    assert text == "Approved 0 fields."


def test_status_cell_shows_a_readable_label(
    qtbot: Any, tmp_path: Path, messages: list[tuple[QMessageBox.Icon, str, str]]
) -> None:
    """The library shows "Not analyzed", not the stored value "unprocessed"."""
    window = build_window(qtbot, tmp_path, with_track=True)

    assert window.file_table.item(0, STATUS_COLUMN).toolTip() == "Not analyzed"
    assert format_status_cell("ready_to_write") == "Ready to save"
    assert format_status_cell("not_a_status") == "not_a_status"


def test_detail_header_says_yes_or_no_for_write_support(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detail header shows Yes or No for write support instead of True or False."""
    # The test covers the header text, so no waveform analysis runs.
    keep_waveform_pool_idle(monkeypatch)
    repository = Repository(DatabaseManager(":memory:"))
    track = create_wav_fixture(tmp_path / "crate" / "a.wav")
    report = ImportService(repository, MetadataTagAdapter()).import_paths([track])
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)

    panel.load_file_asset(report.imported_files[0].id)

    header = panel.header_label.text()
    assert "True" not in header and "False" not in header
    assert header in ("a.wav", "a.wav (can't save tags to this file)")
