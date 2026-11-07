"""Behavioral test: the detail panel keys its audio reload on the file ID alone (D246)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import DetailPanel
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_SECONDS = 2.0
SEEK_SECONDS = 0.5


def test_new_stored_path_under_same_id_keeps_playback(qtbot: Any, tmp_path: Path) -> None:
    """A changed current_path for the same file ID neither reloads nor resets the player."""
    track = create_wav_fixture(tmp_path / "music" / "track.wav", duration_sec=TRACK_SECONDS)
    repository = Repository(DatabaseManager(":memory:"))
    [asset] = ImportService(repository).import_paths([track.parent]).imported_files
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    panel.load_file_asset(asset.id)
    loaded_path = panel.audio_player.current_file_path
    panel.audio_player.seek_to_seconds(SEEK_SECONDS)
    panel.audio_player.toggle_play_pause()

    moved = track.with_name("track-renamed.wav")
    track.rename(moved)
    stored = repository.get_file_by_id(asset.id)
    assert stored is not None
    stored.current_path = str(moved)
    repository.upsert_file_asset(stored)
    panel.load_file_asset(asset.id)

    assert panel.audio_player.current_file_path == loaded_path
    assert panel.audio_player.current_position_sec == pytest.approx(SEEK_SECONDS)
    assert panel.audio_player.play_btn.text() == "Pause"
    assert moved.name in panel.header_label.text()
