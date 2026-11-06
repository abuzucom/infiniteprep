"""Behavioral tests for the background folder import, its progress dialog, and its report."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MetadataTagAdapter,
    TechnicalAndTagMetadata,
)
from infiniteprep.config.settings import AppSettings
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import (
    ImportBatchReport,
    ImportOutcome,
    ImportService,
)
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.folder_import import ImportReportDialog, format_import_report
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_COUNT = 2
GATE_TIMEOUT_SEC = 10.0
SIGNAL_TIMEOUT_MS = 10_000
TIMER_INTERVAL_MS = 10
RESPONSIVE_WAIT_MS = 200


class MemorySettingsManager:
    """Keep application settings in memory for window tests."""

    def __init__(self) -> None:
        self.settings = AppSettings()

    def load(self) -> AppSettings:
        """Return a separate settings value for each load."""
        return replace(self.settings)

    def save(self, settings: AppSettings) -> None:
        """Retain a copy of the last saved settings value."""
        self.settings = replace(settings)


class GatedTagAdapter(MetadataTagAdapter):
    """Hold each metadata read until the test opens the gate, so an import stays running."""

    def __init__(self, gate: threading.Event) -> None:
        super().__init__()
        self.gate = gate

    def read_metadata(self, file_path: Path) -> TechnicalAndTagMetadata:
        """Wait for the gate, then read the file as usual."""
        self.gate.wait(timeout=GATE_TIMEOUT_SEC)
        return super().read_metadata(file_path)


@pytest.fixture
def window_parts(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[MainWindow, Repository, threading.Event, Path]:
    """Build a main window whose imports wait on a gate, plus a folder of tracks."""
    monkeypatch.setattr(main_window_module, "SettingsManager", MemorySettingsManager)
    repository = Repository(DatabaseManager(tmp_path / "library.sqlite3"))
    gate = threading.Event()
    window = MainWindow(
        repository, ImportService(repository, GatedTagAdapter(gate)), ReviewService(repository)
    )
    qtbot.addWidget(window)
    folder = tmp_path / "crate"
    for index in range(TRACK_COUNT):
        create_wav_fixture(folder / f"track{index}.wav", duration_sec=0.5 + index)
    return window, repository, gate, folder


def test_import_runs_off_the_ui_thread_and_commits(qtbot: Any, window_parts: Any) -> None:
    """The UI keeps processing events during an import, and the table fills after commit."""
    window, repository, gate, folder = window_parts
    ticks: list[int] = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(TIMER_INTERVAL_MS)

    with qtbot.waitSignal(window.import_controller.completed, timeout=SIGNAL_TIMEOUT_MS) as done:
        window.start_folder_import(folder)
        assert window.import_controller.is_running()
        qtbot.wait(RESPONSIVE_WAIT_MS)
        assert ticks
        gate.set()

    timer.stop()
    report = done.args[0]
    assert report.outcome is ImportOutcome.COMMITTED
    assert len(repository.list_files()) == TRACK_COUNT
    assert window.file_table.rowCount() == TRACK_COUNT


def test_import_leaves_the_loaded_track_in_place(
    qtbot: Any, window_parts: Any, tmp_path: Path
) -> None:
    """An import neither unloads nor reloads the track in the preview player (D243)."""
    window, _repository, gate, folder = window_parts
    playing = create_wav_fixture(tmp_path / "playing.wav", duration_sec=1.0)
    player = window.detail_panel.audio_player
    player.load_file(playing)

    with qtbot.waitSignal(window.import_controller.completed, timeout=SIGNAL_TIMEOUT_MS):
        window.start_folder_import(folder)
        gate.set()

    assert player.current_file_path == playing


@pytest.mark.parametrize(
    "close_case",
    [("cancel", ImportOutcome.CANCELLED, 0), ("wait", ImportOutcome.COMMITTED, TRACK_COUNT)],
)
def test_closing_during_import_waits_or_cancels(
    qtbot: Any,
    window_parts: Any,
    monkeypatch: pytest.MonkeyPatch,
    close_case: tuple[str, ImportOutcome, int],
) -> None:
    """Close asks to wait or cancel. Either way the window closes once the batch ends."""
    choice, expected_outcome, expected_rows = close_case
    window, repository, gate, folder = window_parts
    monkeypatch.setattr(window, "ask_close_during_import", lambda: choice)
    window.show()

    with qtbot.waitSignal(window.import_controller.completed, timeout=SIGNAL_TIMEOUT_MS) as done:
        window.start_folder_import(folder)
        window.close()
        assert window.isVisible()
        gate.set()

    qtbot.waitUntil(lambda: not window.isVisible(), timeout=SIGNAL_TIMEOUT_MS)
    assert done.args[0].outcome is expected_outcome
    assert len(repository.list_files()) == expected_rows


def test_staying_open_keeps_the_import_running(
    qtbot: Any, window_parts: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dismissing the close prompt keeps the window and lets the import commit."""
    window, repository, gate, folder = window_parts
    monkeypatch.setattr(window, "ask_close_during_import", lambda: "stay")
    window.show()

    with qtbot.waitSignal(window.import_controller.completed, timeout=SIGNAL_TIMEOUT_MS):
        window.start_folder_import(folder)
        window.close()
        gate.set()

    assert window.isVisible()
    assert len(repository.list_files()) == TRACK_COUNT


def test_report_text_lists_counts_and_failed_files() -> None:
    """The plain-text report names each category count and every failed file."""
    report = ImportBatchReport(batch_id="batch-1", scanned_count=4)
    report.unsupported_files.append("/music/cover.jpg")
    report.failed_files.append(("/music/gone.wav", "FileNotFoundError: gone"))

    text = format_import_report(report)

    assert "Imported: 0" in text
    assert "Unsupported: 1" in text
    assert "Failed: 1" in text
    assert "/music/gone.wav: FileNotFoundError: gone" in text


def test_cancelled_report_says_nothing_was_imported() -> None:
    """A rolled-back batch reports that no file was imported."""
    report = ImportBatchReport(batch_id="batch-2", scanned_count=3)
    report.outcome = ImportOutcome.CANCELLED

    assert "Cancelled. No file was imported." in format_import_report(report)


def test_copy_button_puts_the_report_on_the_clipboard(qtbot: Any) -> None:
    """Copy places the same plain text as format_import_report on the clipboard."""
    report = ImportBatchReport(batch_id="batch-3", scanned_count=1)
    dialog = ImportReportDialog(report)
    qtbot.addWidget(dialog)

    dialog.copy_button.click()

    assert QApplication.clipboard().text() == format_import_report(report)
