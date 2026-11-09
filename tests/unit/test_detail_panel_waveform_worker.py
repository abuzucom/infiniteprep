"""Behavioral coverage for rejecting results from stale track selections."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtWidgets import QWidget

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.waveform_analysis import SpectrumWaveform
from infiniteprep.ui.detail_panel import DetailPanel
from tests.fixtures.audio_factory import create_wav_fixture


def test_detail_panel_omits_default_settings_keyword_for_audio_double(
    qtbot: Any, monkeypatch: Any
) -> None:
    """The existing audio preview constructor seam accepts the original keywords."""
    received: list[bool] = []

    class PreviewDouble(QWidget):
        def __init__(self, parent: QWidget, enable_multimedia_backend: bool = False) -> None:
            super().__init__(parent)
            received.append(enable_multimedia_backend)

    monkeypatch.setattr("infiniteprep.ui.detail_panel.AudioPreviewWidget", PreviewDouble)
    repository = Repository(DatabaseManager(":memory:"))
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)

    assert received == [False]


def test_stale_track_result_does_not_replace_selected_waveform(
    qtbot: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """A slower result for the old selection cannot replace the current spectrum."""
    first = create_wav_fixture(tmp_path / "one" / "first.wav")
    second = create_wav_fixture(tmp_path / "two" / "second.wav")
    repository = Repository(DatabaseManager(":memory:"))
    imported = ImportService(repository).import_paths([first, second]).imported_files
    first_asset, second_asset = sorted(imported, key=lambda asset: asset.current_path)
    first_waveform = SpectrumWaveform(*([np.full(2, 0.2, dtype=np.float32)] * 4), 1.0)
    second_waveform = SpectrumWaveform(*([np.full(2, 0.8, dtype=np.float32)] * 4), 1.0)
    first_started = threading.Event()

    def analyze(path: Path, *, cancel_event: threading.Event) -> SpectrumWaveform:
        if path.name == "first.wav":
            first_started.set()
            cancel_event.wait(3)
            return first_waveform
        return second_waveform

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    completed: list[str] = []
    panel.waveform_pool.completed.connect(
        lambda file_id, _generation, _data: completed.append(file_id)
    )
    panel.load_file_asset(first_asset.id)
    qtbot.waitUntil(first_started.is_set, timeout=3000)
    panel.load_file_asset(second_asset.id)

    qtbot.waitUntil(
        lambda: panel.audio_player.waveform_bar.spectrum is second_waveform,
        timeout=3000,
    )
    qtbot.waitUntil(lambda: len(completed) == 1, timeout=3000)
    assert completed == [second_asset.id]
    assert panel.audio_player.waveform_bar.spectrum is second_waveform
    assert panel.current_file_id == second_asset.id
