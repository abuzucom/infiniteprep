"""Tests that an overwritten playlist gets the owner-only mode."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.playlist_exporter import PLAYLIST_FILE_MODE, PlaylistExporter
from tests.fixtures.audio_factory import create_wav_fixture

WORLD_READABLE_MODE = 0o644


@pytest.mark.skipif(not hasattr(os, "fchmod"), reason="The platform has no fchmod.")
def test_overwritten_playlist_loses_group_and_other_bits(tmp_path: Path) -> None:
    """An existing world-readable playlist ends with mode 0o600 after the export."""
    repo = Repository(DatabaseManager(":memory:"))
    track = create_wav_fixture(tmp_path / "crate" / "track.wav", duration_sec=1.0)
    report = ImportService(repo, MutagenAdapter()).import_paths([track])
    file_ids = [asset.id for asset in report.imported_files]
    destination = tmp_path / "out" / "crate.m3u8"
    destination.parent.mkdir()
    destination.write_text("#EXTM3U\n", encoding="utf-8")
    destination.chmod(WORLD_READABLE_MODE)

    report = PlaylistExporter(repo).export_m3u8_report(file_ids, destination)

    assert report.written_count == 1
    assert stat.S_IMODE(destination.stat().st_mode) == PLAYLIST_FILE_MODE
