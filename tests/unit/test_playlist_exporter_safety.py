"""Tests for unsafe track paths and symlinked destinations in the playlist exporter."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.ui.action_handlers import export_playlist_with_feedback
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.qt_dialogs import DialogRecorder


def import_two_tracks(tmp_path: Path) -> tuple[Repository, list[str]]:
    """Import two WAVs, then give the second a stored path with an embedded line break."""
    repo = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repo, MutagenAdapter())
    first = create_wav_fixture(tmp_path / "crate" / "safe.wav", duration_sec=1.0)
    second = create_wav_fixture(tmp_path / "crate" / "second.wav", duration_sec=1.0)
    file_ids = [a.id for a in importer.import_paths([first, second]).imported_files]
    asset = repo.get_file_by_id(file_ids[1])
    assert asset is not None
    asset.current_path = str(tmp_path / "crate" / "evil\n#EXTINF:1,Injected.wav")
    repo.upsert_file_asset(asset)
    return repo, file_ids


def test_track_with_a_line_break_in_its_path_is_left_out_and_reported(tmp_path: Path) -> None:
    """The unsafe track is skipped, the safe track is written, and no line is injected."""
    repo, file_ids = import_two_tracks(tmp_path)
    destination = tmp_path / "out" / "crate.m3u8"

    report = PlaylistExporter(repo).export_m3u8_report(file_ids, destination)

    assert report.written_count == 1
    assert report.skipped_file_ids == [file_ids[1]]
    lines = destination.read_text(encoding="utf-8").splitlines()
    assert lines.count("#EXTINF:1,Injected.wav") == 0
    assert sum(line.startswith("#EXTINF") for line in lines) == 1
    assert str((tmp_path / "crate" / "safe.wav").resolve()) in lines


def test_export_m3u8_keeps_returning_the_destination_path(tmp_path: Path) -> None:
    """The existing export_m3u8 contract still returns the written destination."""
    repo, file_ids = import_two_tracks(tmp_path)
    destination = tmp_path / "out" / "crate.m3u8"

    assert PlaylistExporter(repo).export_m3u8(file_ids, destination) == destination


def test_export_dialog_counts_and_names_the_skipped_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The success dialog counts written tracks and lists the skipped file name."""
    repo, file_ids = import_two_tracks(tmp_path)
    dialogs = DialogRecorder(QMessageBox.StandardButton.Yes)
    dialogs.install(monkeypatch)
    destination = tmp_path / "out" / "crate.m3u8"

    export_playlist_with_feedback(None, PlaylistExporter(repo), file_ids, destination)

    message = dialogs.texts("information")[0]
    assert message.startswith(f"Exported 1 track(s) to {destination}.")
    assert "Skipped 1 track(s) with a line break in the file name:" in message
    assert "evil #EXTINF:1 Injected.wav" in message.replace(",", " ")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits apply only on POSIX.")
def test_new_playlist_is_not_readable_by_group_or_others(tmp_path: Path) -> None:
    """A newly created playlist carries no group or other permission bits."""
    repo, file_ids = import_two_tracks(tmp_path)
    destination = tmp_path / "out" / "crate.m3u8"

    PlaylistExporter(repo).export_m3u8_report(file_ids, destination)

    assert destination.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"),
    reason="The platform has no O_NOFOLLOW flag, so the exporter cannot refuse symlinks.",
)
def test_symlinked_destination_is_refused_and_its_target_kept(tmp_path: Path) -> None:
    """A destination that is a symlink raises OSError and leaves the link target unchanged."""
    repo, file_ids = import_two_tracks(tmp_path)
    target = tmp_path / "elsewhere" / "important.txt"
    target.parent.mkdir()
    target.write_text("keep me", encoding="utf-8")
    link = tmp_path / "out" / "crate.m3u8"
    link.parent.mkdir()
    link.symlink_to(target)

    with pytest.raises(OSError):
        PlaylistExporter(repo).export_m3u8_report(file_ids, link)

    assert target.read_text(encoding="utf-8") == "keep me"
