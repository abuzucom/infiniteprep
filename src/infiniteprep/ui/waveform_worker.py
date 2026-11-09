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

MAX_WAVEFORM_WORKERS = 2
logger = logging.getLogger(__name__)


class _WorkerSignals(QObject):
    """Signals emitted by one analysis task."""

    completed = Signal(str, int, object)
    failed = Signal(str, int, str)
    finished = Signal(object)


class _AnalysisTask(QRunnable):
    """Analyze one path and report its identity with the result."""

    def __init__(self, file_id: str, generation: int, path: Path) -> None:
        super().__init__()
        self.file_id = file_id
        self.generation = generation
        self.path = path
        self.cancel_event = threading.Event()
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        """Run spectrum decoding outside the Qt UI thread."""
        try:
            waveform = analyze_waveform(self.path, cancel_event=self.cancel_event)
        except WaveformAnalysisCancelled:
            return
        except Exception as error:
            logger.exception("Waveform analysis failed for %s", self.path)
            self.signals.failed.emit(self.file_id, self.generation, str(error))
        else:
            if not self.cancel_event.is_set():
                self.signals.completed.emit(self.file_id, self.generation, waveform)
        finally:
            self.signals.finished.emit(id(self))


class WaveformAnalysisPool(QObject):
    """Schedule only current analyses on an instance-local two-job pool."""

    completed = Signal(str, int, object)
    failed = Signal(str, int, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_WAVEFORM_WORKERS)
        self._pool.setExpiryTimeout(-1)
        self._tasks: dict[int, _AnalysisTask] = {}
        self._shutdown = False

    def submit(self, path: Path, file_id: str, generation: int) -> None:
        """Cancel stale work and queue analysis for the current selection only."""
        if self._shutdown:
            return
        self._cancel_tasks()
        task = _AnalysisTask(file_id, generation, Path(path))
        task.signals.completed.connect(self._forward_completed, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._forward_failed, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._task_finished, Qt.ConnectionType.QueuedConnection)
        self._tasks[id(task)] = task
        self._pool.start(task)

    def _cancel_tasks(self) -> None:
        """Cancel active tasks and discard obsolete tasks still waiting in the queue."""
        for task in self._tasks.values():
            task.cancel_event.set()
        self._pool.clear()
        self._tasks.clear()

    @Slot(object)
    def _task_finished(self, task_id: int) -> None:
        """Release a task after its worker has emitted its terminal signal."""
        self._tasks.pop(task_id, None)

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

    @Slot()
    def shutdown(self) -> None:
        """Cancel queued and active work before application shutdown."""
        if self._shutdown:
            return
        self._shutdown = True
        self._cancel_tasks()
        self._pool.waitForDone(2_000)
