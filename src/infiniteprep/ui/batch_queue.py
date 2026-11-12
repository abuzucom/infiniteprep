"""Qt controller that runs queued batches one at a time, each on its own worker thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final, cast

from PySide6.QtCore import QObject, QThread, Signal, Slot

from infiniteprep.services.import_service import ImportBatchReport, ImportOutcome
from infiniteprep.services.job_orchestrator import (
    BatchJobSummary,
    BatchKind,
    BatchQueue,
    BatchState,
    QueuedBatch,
)
from infiniteprep.services.write_service import WriteBatchResult
from infiniteprep.ui.folder_import import FolderImportController

logger = logging.getLogger(__name__)

CancelCheck = Callable[[], bool]
ProgressReport = Callable[[int, int, str], None]
# A batch body. It runs on a worker thread and returns the batch result.
BatchRun = Callable[[CancelCheck, ProgressReport], object]

CANCELLING_TEXT: Final[str] = "Cancelling after the current file."
MAX_LISTED_FAILURES: Final[int] = 10
STATE_TEXT: Final[dict[BatchState, str]] = {
    BatchState.WAITING: "Waiting",
    BatchState.RUNNING: "Running",
    BatchState.COMPLETED: "Completed",
    BatchState.FAILED: "Failed",
    BatchState.CANCELLED: "Cancelled",
}


def _result_counts(result: object) -> str:
    """Return the per-file counts of a batch result, or "" when it carries none."""
    if isinstance(result, BatchJobSummary):
        return f"Processed: {result.completed_files}. Failed: {result.failed_files}."
    if isinstance(result, WriteBatchResult):
        return f"Written: {len(result.records)}. Failed: {len(result.failures)}."
    if isinstance(result, ImportBatchReport):
        return f"Imported: {len(result.imported_files)}. Failed: {len(result.failed_files)}."
    return ""


def _result_failures(result: object) -> list[str]:
    """Return one line per failed file of a batch result."""
    if isinstance(result, BatchJobSummary):
        return [f"{file_id}: {reason}" for file_id, reason in result.errors.items()]
    if isinstance(result, WriteBatchResult):
        return [f"{path}: {reason}" for path, reason in result.failures]
    return []


def format_batch_row(batch: QueuedBatch) -> str:
    """Return the one-line dock text for a batch: label, state, then progress or counts."""
    text = f"{batch.label}: {STATE_TEXT[batch.state]}"
    detail = batch.progress_text or _result_counts(batch.result)
    if detail:
        return f"{text}. {detail}"
    return text


def format_batch_result(batch: QueuedBatch) -> str:
    """Return the plain-text result of a finished batch, with its first failed files."""
    lines = [f"{batch.label}: {STATE_TEXT[batch.state]}"]
    result = batch.result
    if isinstance(result, str) and result:
        lines.append(result)
    counts = _result_counts(result)
    if counts:
        lines.append(counts)
    failures = _result_failures(result)
    if failures:
        lines.append("")
        lines.extend(failures[:MAX_LISTED_FAILURES])
    hidden_count = len(failures) - MAX_LISTED_FAILURES
    if hidden_count > 0:
        lines.append(f"{hidden_count} more failed files are not listed.")
    return "\n".join(lines)


class BatchWorker(QObject):
    """Run one batch body on a worker thread and report through signals."""

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, run: BatchRun, close_connection: Callable[[], None]) -> None:
        """Hold the batch body and the call that closes this thread's database connection."""
        super().__init__()
        self._run = run
        self._close_connection = close_connection
        self._cancel_requested = threading.Event()

    def request_cancel(self) -> None:
        """Ask the batch to stop before its next file."""
        self._cancel_requested.set()

    @Slot()
    def run(self) -> None:
        """Run the batch body, then emit finished with its result or failed with the error.

        The worker closes its own SQLite connection before either signal, so the UI
        thread's join after the signal waits on nothing.
        """
        result: object = None
        error_text = ""
        succeeded = False
        try:
            result = self._run(self._cancel_requested.is_set, self.progress.emit)
            succeeded = True
        # Any exception here would otherwise end the thread silently. The queue reports
        # the batch as failed and starts the next one.
        except Exception as exc:
            logger.exception("Queued batch failed.")
            error_text = f"{type(exc).__name__}: {exc}"
        finally:
            self._close_connection()
        if succeeded:
            self.finished.emit(result)
        else:
            self.failed.emit(error_text)


class BatchQueueController(QObject):
    """Start queued batches in order, one at a time, and report every state change.

    Import batches run through the FolderImportController, which keeps its stuck-file
    prompt. Other batches run a BatchRun on a BatchWorker thread.
    """

    changed = Signal()
    progress_changed = Signal(object)
    batch_finished = Signal(object)
    idle = Signal()

    def __init__(
        self,
        import_controller: FolderImportController,
        close_connection: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        """Keep the import controller and the per-thread database close call."""
        super().__init__(parent)
        self.queue = BatchQueue()
        self.import_controller = import_controller
        self._close_connection = close_connection
        self._thread: QThread | None = None
        self._worker: BatchWorker | None = None
        import_controller.completed.connect(self._on_import_completed)
        import_controller.progress.connect(self._on_import_progress)

    def is_busy(self) -> bool:
        """Return True while a batch runs or waits."""
        return self.queue.is_busy()

    def enqueue_job(self, kind: BatchKind, label: str, run: BatchRun) -> QueuedBatch:
        """Add a batch that runs the given body. It starts at once when the queue is idle."""
        batch = self.queue.enqueue(kind, label, run)
        self._advance()
        return batch

    def enqueue_import(self, paths: list[Path]) -> QueuedBatch:
        """Add a folder import. It starts at once when the queue is idle."""
        names = ", ".join(path.name for path in paths)
        batch = self.queue.enqueue(BatchKind.IMPORT, f"Import {names}", list(paths))
        self._advance()
        return batch

    def cancel(self, batch_id: str) -> None:
        """Remove a waiting batch, or ask the running batch to stop after its current file."""
        running = self.queue.running
        if running is not None and running.id == batch_id:
            self._cancel_running(running)
        else:
            self.queue.cancel_waiting(batch_id)
        self.changed.emit()

    def cancel_all(self) -> None:
        """Cancel every waiting batch and ask the running batch to stop."""
        self.queue.cancel_all_waiting()
        running = self.queue.running
        if running is not None:
            self._cancel_running(running)
        self.changed.emit()

    def move(self, batch_id: str, offset: int) -> bool:
        """Shift a waiting batch by offset places. Return False when it cannot move."""
        moved = self.queue.move(batch_id, offset)
        if moved:
            self.changed.emit()
        return moved

    def reorder_waiting(self, batch_ids: list[str]) -> bool:
        """Put the waiting batches in the given order. A mismatch keeps the old order."""
        reordered = self.queue.reorder_waiting(batch_ids)
        # The view redraws either way, so a refused drag snaps back.
        self.changed.emit()
        return reordered

    def clear_finished(self) -> None:
        """Forget every finished batch."""
        self.queue.clear_finished()
        self.changed.emit()

    def _cancel_running(self, batch: QueuedBatch) -> None:
        """Flag the running batch and pass the cancel request to its worker."""
        batch.cancel_requested = True
        batch.progress_text = CANCELLING_TEXT
        if batch.kind is BatchKind.IMPORT:
            self.import_controller.request_cancel()
        elif self._worker is not None:
            self._worker.request_cancel()

    def _advance(self) -> None:
        """Start the next waiting batch when none runs, then announce the new state."""
        batch = self.queue.start_next()
        while batch is not None and not self._start(batch):
            self.batch_finished.emit(self.queue.finish_running(BatchState.FAILED))
            batch = self.queue.start_next()
        self.changed.emit()
        if not self.queue.is_busy():
            self.idle.emit()

    def _start(self, batch: QueuedBatch) -> bool:
        """Start the batch on its thread. Return False when it could not start."""
        if batch.kind is BatchKind.IMPORT:
            return self.import_controller.start(cast(list[Path], batch.payload))
        self._worker = BatchWorker(cast(BatchRun, batch.payload), self._close_connection)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._thread.start()
        return True

    def _show_progress(self, kind_is_import: bool, done: int, total: int, path: str) -> None:
        """Store the progress text on the running batch unless a cancel is pending."""
        running = self.queue.running
        if running is None or running.cancel_requested:
            return
        if (running.kind is BatchKind.IMPORT) != kind_is_import:
            return
        running.progress_text = f"{done} of {total}: {path}"
        self.progress_changed.emit(running)

    @Slot(int, int, str)
    def _on_import_progress(self, done: int, total: int, path: str) -> None:
        """Show the progress of the running import."""
        self._show_progress(True, done, total, path)

    @Slot(int, int, str)
    def _on_worker_progress(self, done: int, total: int, path: str) -> None:
        """Show the progress of the running worker batch."""
        self._show_progress(False, done, total, path)

    @Slot(object)
    def _on_import_completed(self, report: ImportBatchReport | None) -> None:
        """Record the end of a queued import. An import started elsewhere is ignored."""
        running = self.queue.running
        if running is None or running.kind is not BatchKind.IMPORT:
            return
        if report is None:
            self._finish(BatchState.FAILED, None)
        elif report.outcome is ImportOutcome.CANCELLED:
            self._finish(BatchState.CANCELLED, report)
        else:
            self._finish(BatchState.COMPLETED, report)

    @Slot(object)
    def _on_worker_finished(self, result: object) -> None:
        """Join the worker thread and record the batch as completed or cancelled."""
        self._join_worker()
        running = self.queue.running
        stopped_early = bool(getattr(result, "stopped_early", False))
        cancelled = running is not None and running.cancel_requested and stopped_early
        self._finish(BatchState.CANCELLED if cancelled else BatchState.COMPLETED, result)

    @Slot(str)
    def _on_worker_failed(self, message: str) -> None:
        """Join the worker thread and record the batch as failed with its error text."""
        self._join_worker()
        self._finish(BatchState.FAILED, message)

    def _join_worker(self) -> None:
        """Stop the worker thread and wait for it."""
        thread, worker = self._thread, self._worker
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()

    def _finish(self, state: BatchState, result: object) -> None:
        """Move the running batch to the finished list, then start the next one."""
        batch = self.queue.finish_running(state, result)
        batch.progress_text = ""
        self.batch_finished.emit(batch)
        self._advance()
