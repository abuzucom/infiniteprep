"""A bounded cancel returns while a worker is stuck and discards its late result."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication

from infiniteprep.ui.waveform_worker import WaveformAnalysisPool

BOUNDED_CANCEL_WAIT_MS = 50
WORKER_START_TIMEOUT_MS = 3_000
WORKER_FINISH_TIMEOUT_MS = 3_000
STUCK_READ_MAX_SECONDS = 10.0
GENERATION = 1


def test_bounded_cancel_returns_before_a_stuck_worker_finishes(
    qtbot: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """The caller regains control, and the cancelled worker emits nothing later."""
    started = threading.Event()
    release = threading.Event()

    def analyze(_path: Path, *, cancel_event: threading.Event) -> object:
        started.set()
        # A stalled read does not observe the cancel event.
        release.wait(STUCK_READ_MAX_SECONDS)
        return object()

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    pool = WaveformAnalysisPool()
    results: list[str] = []
    pool.completed.connect(lambda file_id, _generation, _data: results.append(file_id))
    pool.failed.connect(lambda file_id, _generation, _message: results.append(file_id))
    pool.submit(tmp_path / "track.wav", "stuck", GENERATION)
    qtbot.waitUntil(started.is_set, timeout=WORKER_START_TIMEOUT_MS)

    finished = pool.cancel_all(BOUNDED_CANCEL_WAIT_MS)

    assert finished is False
    release.set()
    assert pool.wait_for_done(WORKER_FINISH_TIMEOUT_MS)
    QCoreApplication.processEvents()
    assert results == []
