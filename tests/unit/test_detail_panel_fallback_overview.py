"""The worker supplies a real fallback overview when spectrum analysis fails."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.audio_player import (
    DEFAULT_ENVELOPE_BINS,
    FALLBACK_ENVELOPE_EMPTY,
    extract_waveform_envelope,
)
from infiniteprep.ui.detail_panel import DetailPanel
from infiniteprep.ui.waveform_worker import WaveformAnalysisPool
from tests.fixtures.audio_factory import create_wav_fixture

ANALYSIS_WAIT_TIMEOUT_MS = 3_000
POOL_DRAIN_TIMEOUT_MS = 3_000
GENERATION = 1
FAILURE_TEXT = "decode failed"


def test_failed_analysis_keeps_the_extracted_overview(
    qtbot: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """A spectrum failure leaves the track overview drawn from real audio peaks."""
    source = create_wav_fixture(tmp_path / "track.wav")
    repository = Repository(DatabaseManager(":memory:"))
    asset = ImportService(repository).import_paths([source]).imported_files[0]

    def analyze(_path: Path, *, cancel_event: threading.Event) -> object:
        raise ValueError(FAILURE_TEXT)

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    flat_overview = [FALLBACK_ENVELOPE_EMPTY] * DEFAULT_ENVELOPE_BINS

    panel.load_file_asset(asset.id)

    status_label = panel.audio_player.analysis_status_label
    qtbot.waitUntil(lambda: FAILURE_TEXT in status_label.text(), timeout=ANALYSIS_WAIT_TIMEOUT_MS)
    expected = extract_waveform_envelope(Path(asset.current_path))
    assert expected != flat_overview
    assert panel.audio_player.waveform_bar.envelope == expected


def test_cancelled_task_emits_no_overview(qtbot: Any, tmp_path: Path, monkeypatch: Any) -> None:
    """A task cancelled during the overview pass reports nothing."""
    source = create_wav_fixture(tmp_path / "track.wav")
    pool = WaveformAnalysisPool()

    def cancel_during_extraction(_path: Path, *, cancel_event: threading.Event) -> list[float]:
        cancel_event.set()
        return [FALLBACK_ENVELOPE_EMPTY] * DEFAULT_ENVELOPE_BINS

    monkeypatch.setattr(
        "infiniteprep.ui.waveform_worker.extract_waveform_envelope", cancel_during_extraction
    )
    emitted: list[str] = []
    pool.overview_ready.connect(lambda file_id, _generation, _data: emitted.append(file_id))
    pool.completed.connect(lambda file_id, _generation, _data: emitted.append(file_id))
    pool.failed.connect(lambda file_id, _generation, _message: emitted.append(file_id))

    pool.submit(source, "track", GENERATION)

    assert pool.wait_for_done(POOL_DRAIN_TIMEOUT_MS)
    QCoreApplication.processEvents()
    assert emitted == []
