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

    def analyze(_path: Path) -> str:
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

    qtbot.waitUntil(lambda: len(completed) == 5, timeout=3000)
    assert maximum_active == 2
    assert {result[0] for result in completed} == {f"track-{index}" for index in range(5)}
