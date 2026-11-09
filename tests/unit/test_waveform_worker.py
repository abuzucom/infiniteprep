"""Behavioral coverage for bounded background spectrum analysis."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from infiniteprep.ui.waveform_worker import WaveformAnalysisPool


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
        time.sleep(0.05)
        with lock:
            active -= 1
        return "waveform"

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    pool = WaveformAnalysisPool()
    completed: list[tuple[str, int, object]] = []
    pool.completed.connect(lambda *result: completed.append(result))

    for index in range(5):
        pool.submit(Path(f"track-{index}.wav"), f"track-{index}", index)

    qtbot.waitUntil(lambda: bool(completed), timeout=3000)
    assert maximum_active == 2
    assert [(result[0], result[1]) for result in completed] == [("track-4", 4)]
    pool.shutdown()


def test_shutdown_cancels_running_analysis(qtbot: Any, monkeypatch: Any) -> None:
    """Application shutdown signals active workers and clears queued work."""
    started = threading.Event()
    cancelled = threading.Event()

    def analyze(_path: Path, *, cancel_event: threading.Event) -> str:
        started.set()
        cancel_event.wait(2.0)
        cancelled.set()
        return "waveform"

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    pool = WaveformAnalysisPool()
    pool.submit(Path("track.wav"), "track", 1)
    assert started.wait(1.0)

    pool.shutdown()

    assert cancelled.is_set()
    assert pool.wait_for_done(100)
