"""Behavioral coverage for bounded background spectrum analysis."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from infiniteprep.ui.waveform_worker import MAX_WAVEFORM_WORKERS, WaveformAnalysisPool

ANALYSIS_TASK_COUNT = 5
ANALYSIS_DELAY_SECONDS = 0.05
ANALYSIS_WAIT_TIMEOUT_MS = 3_000
SHUTDOWN_START_TIMEOUT_SECONDS = 1.0
SHUTDOWN_WAIT_TIMEOUT_SECONDS = 2.0
POOL_DRAIN_TIMEOUT_MS = 100


def test_worker_pool_limits_active_analyses(qtbot: Any, monkeypatch: Any) -> None:
    """The UI worker pool never executes more than two analyses at once."""
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def analyze(_path: Path, *, cancel_event: threading.Event) -> str:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(ANALYSIS_DELAY_SECONDS)
        with lock:
            active -= 1
        return "waveform"

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    pool = WaveformAnalysisPool()
    completed: list[tuple[str, int, object]] = []
    pool.completed.connect(lambda *result: completed.append(result))

    for index in range(ANALYSIS_TASK_COUNT):
        pool.submit(Path(f"track-{index}.wav"), f"track-{index}", index)

    qtbot.waitUntil(lambda: bool(completed), timeout=ANALYSIS_WAIT_TIMEOUT_MS)
    assert maximum_active == MAX_WAVEFORM_WORKERS
    last_track_index = ANALYSIS_TASK_COUNT - 1
    assert [(result[0], result[1]) for result in completed] == [
        (f"track-{last_track_index}", last_track_index)
    ]
    pool.shutdown()


def test_shutdown_cancels_running_analysis(qtbot: Any, monkeypatch: Any) -> None:
    """Application shutdown signals active workers and clears queued work."""
    started = threading.Event()
    cancelled = threading.Event()

    def analyze(_path: Path, *, cancel_event: threading.Event) -> str:
        started.set()
        cancel_event.wait(SHUTDOWN_WAIT_TIMEOUT_SECONDS)
        cancelled.set()
        return "waveform"

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    pool = WaveformAnalysisPool()
    pool.submit(Path("track.wav"), "track", 1)
    assert started.wait(SHUTDOWN_START_TIMEOUT_SECONDS)

    pool.shutdown()

    assert cancelled.is_set()
    assert pool.wait_for_done(POOL_DRAIN_TIMEOUT_MS)
