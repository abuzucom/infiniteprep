"""Behavioral test: the detail panel keys its audio reload on the file ID alone (D246)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.waveform_analysis import SpectrumWaveform
from infiniteprep.ui.detail_panel import DetailPanel
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_SECONDS = 2.0
SEEK_SECONDS = 0.5
PEAK_SAMPLE_COUNT = 2
ANALYSIS_WAIT_TIMEOUT_MS = 3_000


def test_new_stored_path_under_same_id_keeps_playback(
    qtbot: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """A changed path refreshes analysis without reloading or resetting playback."""
    track = create_wav_fixture(tmp_path / "music" / "track.wav", duration_sec=TRACK_SECONDS)
    repository = Repository(DatabaseManager(":memory:"))
    [asset] = ImportService(repository).import_paths([track.parent]).imported_files
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    analyzed_paths: list[Path] = []
    peaks = np.ones(PEAK_SAMPLE_COUNT, dtype=np.float32)

    def analyze(path: Path, *, cancel_event: Any) -> SpectrumWaveform:
        analyzed_paths.append(path)
        return SpectrumWaveform(peaks, peaks, peaks, peaks, TRACK_SECONDS)

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    panel.load_file_asset(asset.id)
    # The worker reads the track for the fallback overview before it calls analyze.
    # Windows refuses to rename a file that a worker still holds open.
    qtbot.waitUntil(lambda: track in analyzed_paths, timeout=ANALYSIS_WAIT_TIMEOUT_MS)
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
    qtbot.waitUntil(lambda: moved in analyzed_paths, timeout=ANALYSIS_WAIT_TIMEOUT_MS)

    assert panel.audio_player.current_file_path == loaded_path
    assert analyzed_paths[-1] == moved
    assert panel.audio_player.current_position_sec == pytest.approx(SEEK_SECONDS)
    assert panel.audio_player.play_btn.text() == "Pause"
    assert moved.name in panel.header_label.text()
