"""Behavioral tests for the remaining waveform worker branches."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.services.waveform_analysis import WaveformAnalysisCancelled
from infiniteprep.ui import waveform_worker as worker_module
from infiniteprep.ui.waveform_worker import (
    UNEXPECTED_FAILURE_MESSAGE,
    UNREADABLE_SOURCE_MESSAGE,
    WaveformAnalysisPool,
    _AnalysisTask,
)
from tests.fixtures.call_recorder import CallRecorder


def make_task(cancel_event: threading.Event) -> _AnalysisTask:
    """Return an analysis task with a controllable cancellation event."""
    return _AnalysisTask("track", 1, Path("track.wav"), cancel_event)


def test_task_emits_overview_completion_and_fake_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful task reports the overview, waveform, and fake-lossless result."""
    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )
    monkeypatch.setattr(
        worker_module,
        "analyze_waveform",
        lambda _path, *, cancel_event: "waveform",
    )
    monkeypatch.setattr(
        worker_module,
        "detect_fake_lossless",
        lambda _path, *, method, cancel_event: "fake-result",
    )
    task = make_task(threading.Event())
    overviews = CallRecorder()
    completions = CallRecorder()
    fake_results = CallRecorder()
    task.signals.overview_ready.connect(overviews)
    task.signals.completed.connect(completions)
    task.signals.fake_lossless_ready.connect(fake_results)

    task.run()

    assert overviews == [("track", 1, "overview")]
    assert completions == [("track", 1, "waveform")]
    assert fake_results == [("track", 1, "fake-result")]


def test_task_stops_when_cancelled_after_the_overview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task cancelled after the overview emits no later result."""
    cancel_event = threading.Event()

    def cancel_during_overview(_path: Path, *, cancel_event: threading.Event) -> str:
        cancel_event.set()
        return "overview"

    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        cancel_during_overview,
    )
    task = make_task(cancel_event)
    overviews = CallRecorder()
    completions = CallRecorder()
    task.signals.overview_ready.connect(overviews)
    task.signals.completed.connect(completions)

    task.run()

    assert overviews == []
    assert completions == []


def test_waveform_cancellation_emits_no_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled waveform analysis emits no completion and no fake result."""
    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )

    def cancel_analysis(_path: Path, *, cancel_event: threading.Event) -> str:
        raise WaveformAnalysisCancelled

    monkeypatch.setattr(worker_module, "analyze_waveform", cancel_analysis)
    task = make_task(threading.Event())
    completions = CallRecorder()
    fake_results = CallRecorder()
    task.signals.completed.connect(completions)
    task.signals.fake_lossless_ready.connect(fake_results)

    task.run()

    assert completions == []
    assert fake_results == []


def test_waveform_failure_reports_value_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value error keeps its safe message in the failure signal."""
    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )

    def fail_analysis(_path: Path, *, cancel_event: threading.Event) -> str:
        raise ValueError("The audio range is invalid.")

    monkeypatch.setattr(worker_module, "analyze_waveform", fail_analysis)
    task = make_task(threading.Event())
    failures = CallRecorder(2)
    task.signals.failed.connect(failures)

    task.run()

    assert failures == ["The audio range is invalid."]


def test_waveform_failure_reports_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected waveform error uses the generic failure message."""
    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )

    def fail_analysis(_path: Path, *, cancel_event: threading.Event) -> str:
        raise RuntimeError("private details")

    monkeypatch.setattr(worker_module, "analyze_waveform", fail_analysis)
    task = make_task(threading.Event())
    failures = CallRecorder(2)
    task.signals.failed.connect(failures)

    task.run()

    assert failures == [UNEXPECTED_FAILURE_MESSAGE]


def test_fake_lossless_file_error_reports_safe_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fake-lossless file error uses the unreadable source message."""
    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )
    monkeypatch.setattr(
        worker_module,
        "analyze_waveform",
        lambda _path, *, cancel_event: "waveform",
    )

    def fail_detection(_path: Path, *, method: str | None, cancel_event: threading.Event) -> str:
        raise OSError("private path")

    monkeypatch.setattr(worker_module, "detect_fake_lossless", fail_detection)
    task = make_task(threading.Event())
    failures = CallRecorder(2)
    task.signals.fake_lossless_failed.connect(failures)

    task.run()

    assert failures == [UNREADABLE_SOURCE_MESSAGE]


def test_fake_lossless_cancellation_emits_no_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled fake-lossless pass emits no result."""
    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )
    monkeypatch.setattr(
        worker_module,
        "analyze_waveform",
        lambda _path, *, cancel_event: "waveform",
    )

    def cancel_detection(_path: Path, *, method: str | None, cancel_event: threading.Event) -> str:
        raise WaveformAnalysisCancelled

    monkeypatch.setattr(worker_module, "detect_fake_lossless", cancel_detection)
    task = make_task(threading.Event())
    failures = CallRecorder(2)
    results = CallRecorder()
    task.signals.fake_lossless_failed.connect(failures)
    task.signals.fake_lossless_ready.connect(results)

    task.run()

    assert failures == []
    assert results == []


def test_pool_ignores_submission_after_shutdown() -> None:
    """A pool that has shut down accepts no new work."""
    pool = WaveformAnalysisPool()
    pool.shutdown()

    pool.submit(Path("track.wav"), "track", 1)

    assert pool._active_requests == set()


def test_pool_shutdown_is_idempotent() -> None:
    """A second shutdown leaves the pool closed without another wait."""
    pool = WaveformAnalysisPool()
    pool.shutdown()
    pool.shutdown()

    assert pool._shutdown is True


def test_pool_drops_results_from_an_inactive_request(
    qtbot: Any,
) -> None:
    """A stale worker result does not reach the panel."""
    pool = WaveformAnalysisPool()
    events = CallRecorder()
    pool.overview_ready.connect(events)

    pool._forward_overview_ready("old-track", 1, "overview")

    assert events == []


def test_cancel_all_reports_a_worker_that_did_not_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded cancel reports that a worker is still running."""
    pool = WaveformAnalysisPool()
    monkeypatch.setattr(pool._pool, "waitForDone", lambda _timeout: False)

    assert pool.cancel_all(1) is False
