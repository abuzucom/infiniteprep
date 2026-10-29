"""Tests for control-character skips, suffix handling, and permissions in playlist export."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services import playlist_exporter as exporter_module
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.action_handlers import build_skipped_tracks_text
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture

GROUP_AND_OTHER_BITS = stat.S_IRWXG | stat.S_IRWXO


def import_tracks(tmp_path: Path, count: int) -> tuple[Repository, ImportService, list[str]]:
    """Import the given number of short WAVs into an in-memory library."""
    repo = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repo, MutagenAdapter())
    paths = [
        create_wav_fixture(tmp_path / "crate" / f"track_{index}.wav", duration_sec=1.0)
        for index in range(count)
    ]
    return repo, importer, [a.id for a in importer.import_paths(paths).imported_files]


def store_path(repo: Repository, file_id: str, new_path: str) -> None:
    """Overwrite the stored current_path of one file."""
    asset = repo.get_file_by_id(file_id)
    assert asset is not None
    asset.current_path = new_path
    repo.upsert_file_asset(asset)


@pytest.mark.parametrize("control_char", ["\t", "\x00", "\x1b", "\x7f"])
def test_paths_with_any_control_character_are_skipped(tmp_path: Path, control_char: str) -> None:
    """Tab, NUL, ESC, and DEL paths are left out and reported like line breaks."""
    repo, _, file_ids = import_tracks(tmp_path, 2)
    store_path(repo, file_ids[1], str(tmp_path / "crate" / f"bad{control_char}name.wav"))
    destination = tmp_path / "out" / "crate.m3u8"

    report = PlaylistExporter(repo).export_m3u8_report(file_ids, destination)

    assert report.written_count == 1
    assert report.skipped_file_ids == [file_ids[1]]
    assert control_char not in destination.read_text(encoding="utf-8")


def test_resolved_path_line_is_checked_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean stored path that resolves to a line break is still skipped."""
    repo, _, file_ids = import_tracks(tmp_path, 1)
    real_resolve = Path.resolve

    def resolve_into_line_break(self: Path, strict: bool = False) -> Path:
        resolved = real_resolve(self, strict)
        return resolved.with_name("linked\nname.wav") if self.suffix == ".wav" else resolved

    monkeypatch.setattr(exporter_module.Path, "resolve", resolve_into_line_break)
    destination = tmp_path / "out" / "crate.m3u8"

    report = PlaylistExporter(repo).export_m3u8_report(file_ids, destination)

    assert report.written_count == 0
    assert report.skipped_file_ids == file_ids
    assert "linked" not in destination.read_text(encoding="utf-8")


def test_skipped_text_names_unknown_ids_and_blanks_control_characters(tmp_path: Path) -> None:
    """A stale ID gets an unknown-track label, and control characters become spaces."""
    repo, _, file_ids = import_tracks(tmp_path, 1)
    store_path(repo, file_ids[0], str(tmp_path / "crate" / "tab\there.wav"))

    text = build_skipped_tracks_text(repo, [file_ids[0], "missing-id"])

    assert "tab here.wav" in text
    assert "<unknown track missing-id>" in text


@pytest.mark.parametrize(
    ("chosen_name", "expected_name"),
    [("set.M3U8", "set.M3U8"), ("set.v2", "set.v2.m3u8"), ("notes.txt", "notes.txt.m3u8")],
)
def test_export_button_appends_the_suffix_only_when_missing(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chosen_name: str,
    expected_name: str,
) -> None:
    """An uppercase .M3U8 stays, and any other suffix keeps its name with .m3u8 appended."""
    repo, importer, _ = import_tracks(tmp_path, 1)
    destinations: list[Path] = []
    window = MainWindow(
        repository=repo,
        import_service=importer,
        review_service=ReviewService(repo),
        on_export_playlist=lambda _fids, dest: destinations.append(dest),
    )
    qtbot.addWidget(window)
    chosen = str(tmp_path / chosen_name)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (chosen, ""))

    window.export_playlist_btn.click()

    assert destinations == [tmp_path / expected_name]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits apply only on POSIX.")
def test_new_playlist_has_no_group_or_other_permission_bits(tmp_path: Path) -> None:
    """The created playlist mode clears every group and other permission bit."""
    repo, _, file_ids = import_tracks(tmp_path, 1)
    destination = tmp_path / "out" / "crate.m3u8"

    PlaylistExporter(repo).export_m3u8_report(file_ids, destination)

    assert stat.S_IMODE(destination.stat().st_mode) & GROUP_AND_OTHER_BITS == 0
