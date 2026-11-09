"""Bounded background workers for spectrum waveform analysis."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot

from infiniteprep.services.waveform_analysis import analyze_waveform

MAX_WAVEFORM_WORKERS = 2
_SHARED_POOL = QThreadPool()
_SHARED_POOL.setMaxThreadCount(MAX_WAVEFORM_WORKERS)
_SHARED_POOL.setExpiryTimeout(-1)


class _WorkerSignals(QObject):
    """Signals emitted by one analysis task."""

    completed = Signal(str, int, object)
    failed = Signal(str, int, str)


class _AnalysisTask(QRunnable):
    """Analyze one path and report its identity with the result."""

    def __init__(self, file_id: str, generation: int, path: Path) -> None:
        super().__init__()
        self.file_id = file_id
        self.generation = generation
        self.path = path
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        """Run spectrum decoding outside the Qt UI thread."""
        try:
            waveform = analyze_waveform(self.path)
        except Exception as error:
            self.signals.failed.emit(self.file_id, self.generation, str(error))
            return
        self.signals.completed.emit(self.file_id, self.generation, waveform)


class WaveformAnalysisPool(QObject):
    """Schedule analyses on a pool with a fixed two-job concurrency limit."""

    completed = Signal(str, int, object)
    failed = Signal(str, int, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = _SHARED_POOL

    def submit(self, path: Path, file_id: str, generation: int) -> None:
        """Queue one analysis while retaining the selected track identity."""
        task = _AnalysisTask(file_id, generation, Path(path))
        task.signals.completed.connect(self._forward_completed, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._forward_failed, Qt.ConnectionType.QueuedConnection)
        self._pool.start(task)

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
