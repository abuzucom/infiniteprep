"""A closed detail panel analyzes waveforms again after it reopens."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.waveform_analysis import SpectrumWaveform
from infiniteprep.ui.detail_panel import DetailPanel
from tests.fixtures.audio_factory import create_wav_fixture

PEAK_BIN_COUNT = 2
SPECTRUM_BAND_COUNT = 4
PEAK_LEVEL = 0.5
WAVEFORM_DURATION_SECONDS = 1.0
ANALYSIS_WAIT_TIMEOUT_MS = 3_000


def test_reopened_panel_analyzes_the_selected_track(
    qtbot: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """Closing the panel cancels work without disabling later analysis."""
    source = create_wav_fixture(tmp_path / "track.wav")
    repository = Repository(DatabaseManager(":memory:"))
    asset = ImportService(repository).import_paths([source]).imported_files[0]
    waveform = SpectrumWaveform(
        *([np.full(PEAK_BIN_COUNT, PEAK_LEVEL, dtype=np.float32)] * SPECTRUM_BAND_COUNT),
        WAVEFORM_DURATION_SECONDS,
    )

    def analyze(_path: Path, *, cancel_event: threading.Event) -> SpectrumWaveform:
        return waveform

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    panel.show()
    panel.load_file_asset(asset.id)
    qtbot.waitUntil(
        lambda: panel.audio_player.waveform_bar.spectrum is waveform,
        timeout=ANALYSIS_WAIT_TIMEOUT_MS,
    )

    panel.close()
    panel.audio_player.waveform_bar.spectrum = None
    panel.show()
    panel.load_file_asset(asset.id)

    qtbot.waitUntil(
        lambda: panel.audio_player.waveform_bar.spectrum is waveform,
        timeout=ANALYSIS_WAIT_TIMEOUT_MS,
    )
