"""Tests for the Transcode to XDJ action in the desktop UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import soundfile as sf
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.transcode_batch import resolve_transcode_options
from infiniteprep.ui.action_handlers import run_transcode_with_confirmation
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.qt_dialogs import DialogRecorder


@pytest.fixture
def library(tmp_path: Path) -> tuple[Repository, ImportService, list[str]]:
    """Import a 96 kHz float WAV and a second WAV whose file is then removed.

    The returned IDs list the 96 kHz file first and the removed file second.
    """
    repo = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repo, MutagenAdapter())
    highres = create_wav_fixture(
        tmp_path / "crate" / "96k.wav",
        duration_sec=1.0,
        sample_rate=96000,
        subtype="FLOAT",
        tags={"artist": "Sasha", "title": "Xpander"},
    )
    missing = create_wav_fixture(tmp_path / "crate" / "gone.wav", duration_sec=1.0)
    report = importer.import_paths([highres, missing])
    missing.unlink()
    return repo, importer, [asset.id for asset in report.imported_files]


def test_resolve_transcode_options_falls_back_to_safe_defaults() -> None:
    """Unknown stored values select sibling export and the lossless auto preset."""
    assert resolve_transcode_options("in_place_backed_up", "FORCE_WAV_44K_16B") == (
        TranscodeMode.IN_PLACE_BACKED_UP,
        TranscodePreset.FORCE_WAV_44K_16B,
    )
    assert resolve_transcode_options("overwrite", "bogus") == (
        TranscodeMode.SIBLING_EXPORT,
        TranscodePreset.XDJ_LOSSLESS_AUTO,
    )


def test_pre_rename_preset_name_and_value_still_select_lossless_auto() -> None:
    """The XDJ_RX3 member alias and a stored XDJ_RX3 value both resolve to the renamed preset."""
    assert TranscodePreset.XDJ_RX3_LOSSLESS_AUTO is TranscodePreset.XDJ_LOSSLESS_AUTO
    assert TranscodePreset("XDJ_RX3_LOSSLESS_AUTO") is TranscodePreset.XDJ_LOSSLESS_AUTO
    assert resolve_transcode_options("sibling_export", "XDJ_RX3_LOSSLESS_AUTO") == (
        TranscodeMode.SIBLING_EXPORT,
        TranscodePreset.XDJ_LOSSLESS_AUTO,
    )
    assert [preset.value for preset in TranscodePreset] == [
        "XDJ_LOSSLESS_AUTO",
        "FORCE_WAV_48K_24B",
        "FORCE_WAV_44K_16B",
        "MP3_320_CBR",
    ]


def test_confirmed_batch_transcode_converts_and_reports_the_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, library: Any
) -> None:
    """A confirmed batch writes a 48 kHz sibling and lists the failed file by name."""
    repo, _, file_ids = library
    dialogs = DialogRecorder(QMessageBox.StandardButton.Yes)
    dialogs.install(monkeypatch)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    summary = run_transcode_with_confirmation(
        None, repo, transcoder, file_ids, SettingsManager(tmp_path / "settings.json")
    )

    assert summary is not None
    assert len(summary.results) == 1
    output = summary.results[0].output_path
    assert output.name == "96k_XDJ.wav"
    assert sf.info(str(output)).samplerate == 48000
    assert list(summary.failures) == [file_ids[1]]
    assert "in mode sibling_export" in dialogs.texts("question")[0]
    warning_text = dialogs.texts("warning")[0]
    assert "Transcoded 1 of 2 file(s)." in warning_text
    assert "Failed: gone.wav (FileNotFoundError" in warning_text


def test_declined_batch_transcode_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, library: Any
) -> None:
    """Answering No leaves the crate folder unchanged."""
    repo, _, file_ids = library
    settings_mgr = SettingsManager(tmp_path / "settings.json")
    settings_mgr.save(AppSettings(transcode_mode="in_place_backed_up"))
    dialogs = DialogRecorder(QMessageBox.StandardButton.No)
    dialogs.install(monkeypatch)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    summary = run_transcode_with_confirmation(None, repo, transcoder, file_ids, settings_mgr)

    assert summary is None
    assert "WARNING: in-place mode replaces each original file." in dialogs.texts("question")[0]
    assert sorted(p.name for p in (tmp_path / "crate").iterdir()) == ["96k.wav"]
    assert not (tmp_path / "backups").exists()


def test_main_window_transcode_button_passes_the_visible_files(qtbot, library: Any) -> None:
    """The toolbar button hands every visible file ID to the transcode callback."""
    repo, importer, file_ids = library
    transcode_calls: list[list[str]] = []
    window = MainWindow(
        repository=repo,
        import_service=importer,
        review_service=ReviewService(repo),
        on_transcode_batch=transcode_calls.append,
    )
    qtbot.addWidget(window)

    window.transcode_btn.click()

    assert transcode_calls == [window.visible_file_ids]
    assert sorted(transcode_calls[0]) == sorted(file_ids)
