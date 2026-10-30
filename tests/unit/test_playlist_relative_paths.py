"""Tests for relative track paths in playlist export and the Settings toggle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services import playlist_exporter as exporter_module
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.ui.settings_dialog import SettingsDialog
from tests.fixtures.audio_factory import create_wav_fixture


def import_tracks(paths: list[Path]) -> tuple[Repository, list[str]]:
    """Create each WAV and import it into an in-memory library, keeping the order."""
    repo = Repository(DatabaseManager(":memory:"))
    created = [create_wav_fixture(path, duration_sec=1.0) for path in paths]
    report = ImportService(repo, MutagenAdapter()).import_paths(created)
    by_path = {Path(asset.current_path).resolve(): asset.id for asset in report.imported_files}
    return repo, [by_path[path.resolve()] for path in created]


def path_lines(playlist: Path) -> list[str]:
    """Return the track lines of a playlist, skipping the header and #EXTINF lines."""
    lines = playlist.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if not line.startswith("#")]


def test_relative_export_writes_paths_from_the_playlist_folder(tmp_path: Path) -> None:
    """Tracks beside, below, and above the playlist folder get relative POSIX lines."""
    repo, file_ids = import_tracks(
        [
            tmp_path / "crate" / "beside.wav",
            tmp_path / "crate" / "deep" / "below.wav",
            tmp_path / "other" / "sibling.wav",
        ]
    )
    destination = tmp_path / "crate" / "set.m3u8"

    PlaylistExporter(repo).export_m3u8_report(file_ids, destination, relative_paths=True)

    assert path_lines(destination) == ["beside.wav", "deep/below.wav", "../other/sibling.wav"]


def test_default_export_keeps_absolute_paths(tmp_path: Path) -> None:
    """Without the keyword, existing callers still get absolute resolved paths."""
    track = tmp_path / "crate" / "beside.wav"
    repo, file_ids = import_tracks([track])
    destination = tmp_path / "crate" / "set.m3u8"

    PlaylistExporter(repo).export_m3u8(file_ids, destination)

    assert path_lines(destination) == [str(track.resolve())]


def test_relative_line_starting_with_hash_gets_a_dot_slash_prefix(tmp_path: Path) -> None:
    """A file name that starts with # cannot turn into a comment or directive line."""
    repo, file_ids = import_tracks([tmp_path / "crate" / "#1 hit.wav"])
    destination = tmp_path / "crate" / "set.m3u8"

    PlaylistExporter(repo).export_m3u8_report(file_ids, destination, relative_paths=True)

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert "./#1 hit.wav" in lines
    assert not any(line.startswith("#1") for line in lines)


def test_track_on_another_drive_falls_back_to_the_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When relpath cannot cross drives, the line stays absolute."""
    track = tmp_path / "crate" / "beside.wav"
    repo, file_ids = import_tracks([track])
    destination = tmp_path / "crate" / "set.m3u8"

    def refuse_cross_drive(_path: str, _start: object = None) -> str:
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(exporter_module.os.path, "relpath", refuse_cross_drive)

    PlaylistExporter(repo).export_m3u8_report(file_ids, destination, relative_paths=True)

    assert path_lines(destination) == [str(track.resolve())]


def test_relative_paths_default_on_and_older_settings_files_load(tmp_path: Path) -> None:
    """New settings default to relative paths, and a file without the key loads that default."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"transcode_mode": "sibling_export"}), encoding="utf-8")

    assert AppSettings().playlist_relative_paths is True
    assert SettingsManager(settings_path).load().playlist_relative_paths is True


def test_settings_dialog_saves_the_relative_paths_checkbox(qtbot, tmp_path: Path) -> None:
    """Unchecking the box and saving stores absolute-path mode."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(dialog)
    assert dialog.playlist_relative_checkbox.isChecked()

    dialog.playlist_relative_checkbox.setChecked(False)
    dialog.save_settings()

    assert manager.load().playlist_relative_paths is False
