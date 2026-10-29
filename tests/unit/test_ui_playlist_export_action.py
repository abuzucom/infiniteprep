"""Tests for the Export Playlist (.m3u8) action in the desktop UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.action_handlers import export_playlist_with_feedback
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.qt_dialogs import DialogRecorder


@pytest.fixture
def crate(tmp_path: Path) -> tuple[Repository, ImportService, list[str]]:
    """Import one tagged WAV and one untagged WAV into an in-memory library."""
    repo = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repo, MutagenAdapter())
    tagged = create_wav_fixture(
        tmp_path / "crate" / "xpander.wav",
        duration_sec=1.0,
        tags={"artist": "Sasha", "title": "Xpander"},
    )
    untagged = create_wav_fixture(tmp_path / "crate" / "untitled_dub.wav", duration_sec=1.0)
    report = importer.import_paths([tagged, untagged])
    return repo, importer, [asset.id for asset in report.imported_files]


def test_export_button_appends_m3u8_and_passes_visible_files(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crate: Any
) -> None:
    """A destination without the suffix gains .m3u8, and the visible IDs reach the callback."""
    repo, importer, file_ids = crate
    export_calls: list[tuple[list[str], Path]] = []
    window = MainWindow(
        repository=repo,
        import_service=importer,
        review_service=ReviewService(repo),
        on_export_playlist=lambda fids, dest: export_calls.append((fids, dest)),
    )
    qtbot.addWidget(window)
    chosen = str(tmp_path / "exports" / "friday_set")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (chosen, ""))

    window.export_playlist_btn.click()

    assert export_calls == [(window.visible_file_ids, tmp_path / "exports" / "friday_set.m3u8")]
    assert sorted(export_calls[0][0]) == sorted(file_ids)


def test_cancelled_save_dialog_exports_nothing(
    qtbot, monkeypatch: pytest.MonkeyPatch, crate: Any
) -> None:
    """Closing the save dialog without a path makes no export call."""
    repo, importer, _ = crate
    export_calls: list[tuple[list[str], Path]] = []
    window = MainWindow(
        repository=repo,
        import_service=importer,
        review_service=ReviewService(repo),
        on_export_playlist=lambda fids, dest: export_calls.append((fids, dest)),
    )
    qtbot.addWidget(window)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: ("", ""))

    window.export_playlist_btn.click()

    assert export_calls == []


def test_export_writes_the_crate_and_reports_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crate: Any
) -> None:
    """The export writes an extended M3U8 with tagged and filename-derived entries."""
    repo, _, file_ids = crate
    dialogs = DialogRecorder(QMessageBox.StandardButton.Yes)
    dialogs.install(monkeypatch)
    destination = tmp_path / "exports" / "crate.m3u8"

    written = export_playlist_with_feedback(None, PlaylistExporter(repo), file_ids, destination)

    assert written == destination
    lines = destination.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["#EXTM3U", "#EXTENC:UTF-8"]
    assert "#EXTINF:1,Sasha - Xpander" in lines
    assert any(line.endswith("untitled_dub") for line in lines if line.startswith("#EXTINF"))
    assert str((tmp_path / "crate" / "xpander.wav").resolve()) in lines
    assert dialogs.texts("information") == [f"Exported 2 track(s) to {destination}."]


def test_export_to_an_unwritable_path_reports_the_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crate: Any
) -> None:
    """An OSError from the write shows a warning and returns None."""
    repo, _, file_ids = crate
    dialogs = DialogRecorder(QMessageBox.StandardButton.Yes)
    dialogs.install(monkeypatch)
    occupied = tmp_path / "exports" / "crate.m3u8"
    occupied.mkdir(parents=True)

    written = export_playlist_with_feedback(None, PlaylistExporter(repo), file_ids, occupied)

    assert written is None
    assert dialogs.texts("information") == []
    assert "Playlist export failed" in dialogs.texts("warning")[0]
