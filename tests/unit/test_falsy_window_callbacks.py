"""Tests that window callbacks run even when the passed callable is falsy.

An empty CallRecorder is a list and therefore falsy. A truthiness check would treat it
as a missing callback.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import DetailPanel
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.memory_repository import build_memory_repository

FILE_ID_CALLBACKS = {
    "on_analyze_batch": MainWindow._trigger_analyze_all,
    "on_open_write_dialog": MainWindow._trigger_open_write_dialog,
    "on_transcode_batch": MainWindow._trigger_transcode_batch,
}
NO_ARGUMENT_CALLBACKS = {
    "on_open_journal_dialog": MainWindow._trigger_open_journal,
    "on_open_settings_dialog": MainWindow._trigger_open_settings,
}


@pytest.fixture
def library(tmp_path: Path) -> tuple[Repository, ImportService, list[str]]:
    """Import one WAV file into an in-memory library."""
    repository = build_memory_repository()
    importer = ImportService(repository, MetadataTagAdapter())
    track = create_wav_fixture(tmp_path / "crate" / "track.wav", duration_sec=1.0)
    report = importer.import_paths([track])
    return repository, importer, [asset.id for asset in report.imported_files]


def _build_window(qtbot: Any, library: Any, **callbacks: CallRecorder) -> MainWindow:
    """Return a main window over the library with the given callbacks."""
    repository, importer, _file_ids = library
    window = MainWindow(repository, importer, ReviewService(repository), **callbacks)
    qtbot.addWidget(window)
    return window


@pytest.mark.parametrize("name", sorted(FILE_ID_CALLBACKS))
def test_empty_recorder_gets_file_ids(qtbot: Any, library: Any, name: str) -> None:
    """A falsy file callback receives the visible file IDs."""
    recorder = CallRecorder(0)
    window = _build_window(qtbot, library, **{name: recorder})
    trigger: Callable[[MainWindow], None] = FILE_ID_CALLBACKS[name]

    trigger(window)

    assert [sorted(file_ids) for file_ids in recorder] == [sorted(library[2])]


@pytest.mark.parametrize("name", sorted(NO_ARGUMENT_CALLBACKS))
def test_empty_recorder_opens_dialog(qtbot: Any, library: Any, name: str) -> None:
    """A falsy dialog callback runs once."""
    recorder = CallRecorder()
    window = _build_window(qtbot, library, **{name: recorder})
    trigger: Callable[[MainWindow], None] = NO_ARGUMENT_CALLBACKS[name]

    trigger(window)

    assert recorder == [()]


def test_empty_recorder_hears_panel_change(qtbot: Any) -> None:
    """A falsy change callback hears a row action refresh."""
    repository = build_memory_repository()
    recorder = CallRecorder()
    panel = DetailPanel(repository, ReviewService(repository), on_change_callback=recorder)
    qtbot.addWidget(panel)

    panel._refresh_after_row_action()

    assert recorder == [()]
