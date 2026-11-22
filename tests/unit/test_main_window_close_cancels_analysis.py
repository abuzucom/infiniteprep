"""Closing the main window cancels detail-panel analysis still in flight."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.fake_lossless import (
    TRACE_SPECTRAL_CUTOFF,
    FakeLosslessMethod,
    FakeLosslessResult,
)
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.waveform_stub import SIGNAL_FILE_ID_INDEX, SIGNAL_GENERATION_INDEX

FILE_ID = "file-1"
GENERATION = 1
SAMPLE_RATE_HZ = 44100


def _suspect_result() -> FakeLosslessResult:
    """Return a suspect detection result with one spectral cutoff trace."""
    return FakeLosslessResult(
        is_suspect=True,
        cutoff_hz=16000.0,
        sample_rate=SAMPLE_RATE_HZ,
        nyquist_hz=SAMPLE_RATE_HZ / 2.0,
        traces=(TRACE_SPECTRAL_CUTOFF,),
        confidence=0.82,
        method=FakeLosslessMethod.SIGNAL_ANALYSIS.value,
    )


def test_close_drops_late_detail_results(qtbot: Any, tmp_path: Path, monkeypatch: Any) -> None:
    """A result queued before the window closes never reaches the detail panel."""
    repository = Repository(DatabaseManager(":memory:"))
    window = MainWindow(
        repository=repository,
        import_service=ImportService(repository),
        review_service=ReviewService(repository),
    )
    qtbot.addWidget(window)
    pool = window.detail_panel.waveform_pool
    monkeypatch.setattr(pool._pool, "start", CallRecorder())
    delivered = CallRecorder(SIGNAL_FILE_ID_INDEX, SIGNAL_GENERATION_INDEX)
    pool.fake_lossless_ready.connect(delivered)
    pool.submit(tmp_path / "track.flac", FILE_ID, GENERATION)

    window.close()
    pool._forward_fake_lossless_ready(FILE_ID, GENERATION, _suspect_result())

    assert delivered == []
