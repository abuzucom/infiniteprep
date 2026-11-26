"""Fake-lossless smoothing edges, worker failures, and late result delivery."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.fake_lossless import (
    FFT_SIZE,
    TRACE_SPECTRAL_CUTOFF,
    FakeLosslessMethod,
    FakeLosslessResult,
    _detect_spectral_cutoff,
    _smooth_profile,
)
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import DetailPanel
from infiniteprep.ui.waveform_worker import WaveformAnalysisPool, _AnalysisTask
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.waveform_stub import SIGNAL_FILE_ID_INDEX, SIGNAL_GENERATION_INDEX

SAMPLE_RATE_HZ = 44100
NYQUIST_HZ = SAMPLE_RATE_HZ / 2.0
STOPBAND_DB = -80.0
PASSBAND_EDGE_HZ = 16000.0
CUTOFF_TOLERANCE_HZ = 500.0
EDGE_LEVEL_TOLERANCE_DB = 1e-6
FILE_ID = "file-1"
GENERATION = 1


def _profile_freqs() -> np.ndarray:
    """Return the FFT bin frequencies the detector analyzes."""
    return np.fft.rfftfreq(FFT_SIZE, d=1.0 / SAMPLE_RATE_HZ)


def _suspect_result() -> FakeLosslessResult:
    """Return a suspect detection result with one spectral cutoff trace."""
    return FakeLosslessResult(
        is_suspect=True,
        cutoff_hz=PASSBAND_EDGE_HZ,
        sample_rate=SAMPLE_RATE_HZ,
        nyquist_hz=NYQUIST_HZ,
        traces=(TRACE_SPECTRAL_CUTOFF,),
        confidence=0.82,
        method=FakeLosslessMethod.SIGNAL_ANALYSIS.value,
    )


def _stored_fake_lossless_results(repository: Repository, file_id: str) -> list[Any]:
    """Return stored analysis results written by the fake-lossless analyzer."""
    return [
        result
        for result in repository.list_analysis_results(file_id)
        if result.analyzer_name == "fake_lossless"
    ]


def test_smoothing_keeps_flat_edges_flat() -> None:
    """Smoothing a constant dB profile returns the same level in every bin."""
    freqs = _profile_freqs()
    flat = np.full(len(freqs), STOPBAND_DB)

    smoothed = _smooth_profile(freqs, flat)

    assert len(smoothed) == len(flat)
    assert np.max(np.abs(smoothed - STOPBAND_DB)) < EDGE_LEVEL_TOLERANCE_DB


def test_spectral_cutoff_ignores_nyquist_bin() -> None:
    """A deep stopband near Nyquist yields the passband edge as the cutoff."""
    freqs = _profile_freqs()
    profile = np.where(freqs < PASSBAND_EDGE_HZ, 0.0, STOPBAND_DB)

    cutoff_hz, is_suspect, _score = _detect_spectral_cutoff(
        freqs, _smooth_profile(freqs, profile), NYQUIST_HZ
    )

    assert is_suspect
    assert abs(cutoff_hz - PASSBAND_EDGE_HZ) < CUTOFF_TOLERANCE_HZ


def test_detection_failure_emits_signal(tmp_path: Path) -> None:
    """An unreadable source reports a fake-lossless failure instead of silence."""
    task = _AnalysisTask(FILE_ID, GENERATION, tmp_path / "missing.flac", threading.Event())
    failures = CallRecorder()
    task.signals.fake_lossless_failed.connect(failures)

    task._run_fake_lossless()

    assert len(failures) == 1
    assert failures[0][:2] == (FILE_ID, GENERATION)
    assert failures[0][2]


def test_unexpected_detection_error_propagates(tmp_path: Path, monkeypatch: Any) -> None:
    """A programming error in detection reaches the worker boundary."""

    def broken_detect(*_args: object, **_kwargs: object) -> FakeLosslessResult:
        raise TypeError("detector defect")

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.detect_fake_lossless", broken_detect)
    task = _AnalysisTask(FILE_ID, GENERATION, tmp_path / "track.flac", threading.Event())

    with pytest.raises(TypeError):
        task._run_fake_lossless()


def test_pool_drops_results_after_cancel(qtbot: Any, tmp_path: Path, monkeypatch: Any) -> None:
    """A queued result delivered after cancel_all never reaches listeners."""
    pool = WaveformAnalysisPool()
    monkeypatch.setattr(pool._pool, "start", CallRecorder())
    delivered = CallRecorder(SIGNAL_FILE_ID_INDEX, SIGNAL_GENERATION_INDEX)
    pool.fake_lossless_ready.connect(delivered)
    pool.submit(tmp_path / "track.flac", FILE_ID, GENERATION)

    pool._forward_fake_lossless_ready(FILE_ID, GENERATION, _suspect_result())
    pool.cancel_all()
    pool._forward_fake_lossless_ready(FILE_ID, GENERATION, _suspect_result())

    assert delivered == [(FILE_ID, GENERATION)]


def test_failure_replaces_stale_warning(qtbot: Any, tmp_path: Path) -> None:
    """A failed re-analysis clears the previously stored fake-lossless warning."""
    source = create_wav_fixture(tmp_path / "track.wav")
    repository = Repository(DatabaseManager(":memory:"))
    asset = ImportService(repository).import_paths([source]).imported_files[0]
    panel = DetailPanel(repository, ReviewService(repository), enable_audio_backend=False)
    qtbot.addWidget(panel)
    panel.current_file_id = asset.id
    panel._analysis_generation = GENERATION
    panel._latest_generation_by_file[asset.id] = GENERATION

    panel._on_fake_lossless_ready(asset.id, GENERATION, _suspect_result())
    stored = _stored_fake_lossless_results(repository, asset.id)
    assert len(stored[0].warnings) == 1

    panel._on_fake_lossless_failed(asset.id, GENERATION, "Unable to read the audio file.")

    stored = _stored_fake_lossless_results(repository, asset.id)
    assert len(stored) == 1
    assert stored[0].warnings == []
    assert stored[0].configuration["status"] == "failed"
    assert "Suspected fake lossless" not in panel.warnings_label.text()
