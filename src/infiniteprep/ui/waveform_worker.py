"""Bounded background workers for spectrum waveform analysis."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot

from infiniteprep.services.waveform_analysis import (
    WaveformAnalysisCancelled,
    analyze_waveform,
)
from infiniteprep.ui.audio_player import extract_waveform_envelope

MAX_WAVEFORM_WORKERS = 2
CANCEL_WAIT_MS = 2_000
UNEXPECTED_FAILURE_MESSAGE = "Spectrum analysis failed unexpectedly. Check the application logs."
UNREADABLE_SOURCE_MESSAGE = (
    "Unable to read the selected audio file. Check that it exists and is accessible."
)
logger = logging.getLogger(__name__)


def _failure_message(error: Exception) -> str:
    """Return a user-safe message while the worker logs full error details."""
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, OSError):
        return UNREADABLE_SOURCE_MESSAGE
    return UNEXPECTED_FAILURE_MESSAGE


class _WorkerSignals(QObject):
    """Signals emitted by one analysis task."""

    overview_ready = Signal(str, int, object)
    completed = Signal(str, int, object)
    failed = Signal(str, int, str)


class _AnalysisTask(QRunnable):
    """Analyze one path and report its identity with the result."""

    def __init__(
        self,
        file_id: str,
        generation: int,
        path: Path,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.file_id = file_id
        self.generation = generation
        self.path = path
        self.cancel_event = cancel_event
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        """Run spectrum decoding outside the Qt UI thread."""
        if self.cancel_event.is_set():
            return
        # The cheap overview pass runs first. The overview stays meaningful while the
        # spectrum pass runs and after a spectrum failure.
        envelope = extract_waveform_envelope(self.path, cancel_event=self.cancel_event)
        if self.cancel_event.is_set():
            return
        self.signals.overview_ready.emit(self.file_id, self.generation, envelope)
        try:
            waveform = analyze_waveform(self.path, cancel_event=self.cancel_event)
        except WaveformAnalysisCancelled:
            return
        except Exception as error:
            # Cancellation intentionally derives from BaseException, so it cannot be
            # consumed by this ordinary analysis-failure boundary.
            logger.exception("Waveform analysis failed for %s", self.path)
            if not self.cancel_event.is_set():
                self.signals.failed.emit(self.file_id, self.generation, _failure_message(error))
        else:
            if not self.cancel_event.is_set():
                self.signals.completed.emit(self.file_id, self.generation, waveform)


class WaveformAnalysisPool(QObject):
    """Schedule only current analyses on an instance-local two-job pool."""

    overview_ready = Signal(str, int, object)
    completed = Signal(str, int, object)
    failed = Signal(str, int, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_WAVEFORM_WORKERS)
        self._pool.setExpiryTimeout(-1)
        self._cancel_events: list[threading.Event] = []
        self._shutdown = False

    def submit(self, path: Path, file_id: str, generation: int) -> None:
        """Cancel stale work and queue analysis for the current selection only."""
        if self._shutdown:
            return
        self._cancel_tasks()
        cancel_event = threading.Event()
        task = _AnalysisTask(file_id, generation, Path(path), cancel_event)
        task.signals.overview_ready.connect(
            self._forward_overview_ready, Qt.ConnectionType.QueuedConnection
        )
        task.signals.completed.connect(self._forward_completed, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._forward_failed, Qt.ConnectionType.QueuedConnection)
        self._cancel_events.append(cancel_event)
        self._pool.start(task)

    def _cancel_tasks(self) -> None:
        """Cancel active tasks and discard obsolete tasks still waiting in the queue."""
        for cancel_event in self._cancel_events:
            cancel_event.set()
        self._pool.clear()
        self._cancel_events.clear()

    @Slot(str, int, object)
    def _forward_overview_ready(self, file_id: str, generation: int, envelope: object) -> None:
        """Forward a fallback overview on the pool object's thread."""
        self.overview_ready.emit(file_id, generation, envelope)

    @Slot(str, int, object)
    def _forward_completed(self, file_id: str, generation: int, waveform: object) -> None:
        """Forward a completed waveform on the pool object's thread."""
        self.completed.emit(file_id, generation, waveform)

    @Slot(str, int, str)
    def _forward_failed(self, file_id: str, generation: int, message: str) -> None:
        """Forward a worker failure on the pool object's thread."""
        self.failed.emit(file_id, generation, message)

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        """Wait for every submitted task to finish."""
        return self._pool.waitForDone(milliseconds)

    def cancel_all(self, timeout_ms: int = -1) -> bool:
        """Cancel queued and active work and wait for it. The pool stays usable.

        A nonnegative timeout bounds the wait. The return value is False when a
        worker is still inside a blocking read. Its cancel event stays set, so the
        worker emits nothing when it finishes.
        """
        self._cancel_tasks()
        finished = self._pool.waitForDone(timeout_ms)
        if not finished:
            logger.warning(
                "Waveform workers still run after %d ms. Their results are discarded.",
                timeout_ms,
            )
        return finished

    @Slot()
    def shutdown(self) -> None:
        """Cancel queued and active work before application shutdown.

        The wait is bounded so a read stalled on a slow volume cannot hang quit.
        A worker that outlives the wait emits nothing, because its cancel event
        stays set.
        """
        if self._shutdown:
            return
        self._shutdown = True
        self.cancel_all(CANCEL_WAIT_MS)
