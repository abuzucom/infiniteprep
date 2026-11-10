"""Behavioral coverage for bounded background spectrum analysis."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from infiniteprep.ui.waveform_worker import (
    MAX_WAVEFORM_WORKERS,
    WaveformAnalysisPool,
    _AnalysisTask,
)

ANALYSIS_TASK_COUNT = 5
ANALYSIS_WAIT_TIMEOUT_MS = 3_000
ANALYSIS_START_TIMEOUT_SECONDS = 1.0
ANALYSIS_RELEASE_TIMEOUT_SECONDS = 3.0
SHUTDOWN_START_TIMEOUT_SECONDS = 1.0
SHUTDOWN_WAIT_TIMEOUT_SECONDS = 2.0
POOL_DRAIN_TIMEOUT_MS = 100
UNEXPECTED_FAILURE_MESSAGE = "Spectrum analysis failed unexpectedly. Check the application logs."
UNREADABLE_SOURCE_MESSAGE = (
    "Unable to read the selected audio file. Check that it exists and is accessible."
)


def test_analysis_task_skips_analysis_when_already_cancelled(monkeypatch: Any) -> None:
    """A task canceled before it starts never calls the analyzer."""
    cancel_event = threading.Event()
    cancel_event.set()

    def unexpected_analysis(_path: Path, *, cancel_event: threading.Event) -> str:
        raise AssertionError("A canceled task must not analyze its path.")

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", unexpected_analysis)
    task = _AnalysisTask("track", 1, Path("track.wav"), cancel_event)
    completed: list[object] = []
    failed: list[str] = []
    task.signals.completed.connect(lambda *_args: completed.append(_args))
    task.signals.failed.connect(lambda _file_id, _generation, message: failed.append(message))

    task.run()

    assert completed == []
    assert failed == []


def test_worker_logs_unexpected_failure_and_emits_safe_message(
    qtbot: Any, monkeypatch: Any, caplog: Any
) -> None:
    """Unexpected details reach logs without exposing exception text to the UI."""
    private_detail = "private path /library/secret.wav"

    def fail_analysis(_path: Path, *, cancel_event: threading.Event) -> str:
        raise RuntimeError(private_detail)

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", fail_analysis)
    pool = WaveformAnalysisPool()
    failures: list[str] = []
    pool.failed.connect(lambda _file_id, _generation, message: failures.append(message))
    pool.submit(Path("track.wav"), "track", 1)

    qtbot.waitUntil(lambda: bool(failures), timeout=ANALYSIS_WAIT_TIMEOUT_MS)
    pool.shutdown()

    assert failures == [UNEXPECTED_FAILURE_MESSAGE]
    assert private_detail in caplog.text
    assert private_detail not in failures[0]


def test_worker_emits_safe_message_for_file_access_failure(qtbot: Any, monkeypatch: Any) -> None:
    """Filesystem details remain in logs while the UI gets a recovery hint."""

    def fail_analysis(_path: Path, *, cancel_event: threading.Event) -> str:
        raise PermissionError("/library/private.wav: permission denied")

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", fail_analysis)
    pool = WaveformAnalysisPool()
    failures: list[str] = []
    pool.failed.connect(lambda _file_id, _generation, message: failures.append(message))
    pool.submit(Path("track.wav"), "track", 1)

    qtbot.waitUntil(lambda: bool(failures), timeout=ANALYSIS_WAIT_TIMEOUT_MS)
    pool.shutdown()

    assert failures == [UNREADABLE_SOURCE_MESSAGE]


def test_worker_pool_limits_active_analyses(qtbot: Any, monkeypatch: Any) -> None:
    """The UI worker pool never executes more than two analyses at once."""
    active = 0
    maximum_active = 0
    started_count = 0
    lock = threading.Lock()
    first_started = threading.Event()
    two_started = threading.Event()
    release_analyses = threading.Event()

    def analyze(_path: Path, *, cancel_event: threading.Event) -> str:
        nonlocal active, maximum_active, started_count
        with lock:
            active += 1
            started_count += 1
            maximum_active = max(maximum_active, active)
            if started_count == 1:
                first_started.set()
            if active == MAX_WAVEFORM_WORKERS:
                two_started.set()
        release_analyses.wait(ANALYSIS_RELEASE_TIMEOUT_SECONDS)
        with lock:
            active -= 1
        return "waveform"

    monkeypatch.setattr("infiniteprep.ui.waveform_worker.analyze_waveform", analyze)
    pool = WaveformAnalysisPool()
    completed: list[tuple[str, int, object]] = []
    pool.completed.connect(lambda *result: completed.append(result))

    try:
        pool.submit(Path("track-0.wav"), "track-0", 0)
        assert first_started.wait(ANALYSIS_START_TIMEOUT_SECONDS)
        pool.submit(Path("track-1.wav"), "track-1", 1)
        assert two_started.wait(ANALYSIS_START_TIMEOUT_SECONDS)

        for index in range(2, ANALYSIS_TASK_COUNT):
            pool.submit(Path(f"track-{index}.wav"), f"track-{index}", index)

        release_analyses.set()
        qtbot.waitUntil(lambda: bool(completed), timeout=ANALYSIS_WAIT_TIMEOUT_MS)
        assert maximum_active == MAX_WAVEFORM_WORKERS
        last_track_index = ANALYSIS_TASK_COUNT - 1
        assert [(result[0], result[1]) for result in completed] == [
            (f"track-{last_track_index}", last_track_index)
        ]
    finally:
        release_analyses.set()
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
