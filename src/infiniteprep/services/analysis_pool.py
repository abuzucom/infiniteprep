"""Process pool that runs the local BPM and key analysis on worker processes.

The pool keeps the tasks in submission order. A caller submits the files it wants
analyzed and takes the outcomes in the same order. A worker that dies takes the pool
down with it, so the pool rebuilds itself and retries the unfinished files.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import (
    BrokenExecutor,
    Future,
    ProcessPoolExecutor,
    wait,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from infiniteprep.adapters.analyzers.local_dsp import LocalDspAnalyzer
from infiniteprep.config.settings import (
    DEFAULT_ANALYSIS_WORKERS,
    MAX_ANALYSIS_WORKERS,
    MIN_ANALYSIS_WORKERS,
)
from infiniteprep.domain.models import AnalysisResult
from infiniteprep.services.analysis_worker import AnalyzerFactory, analyze_track

logger = logging.getLogger(__name__)

# Seconds between the cancel checks while a result is pending.
CANCEL_POLL_SECONDS: Final[float] = 0.2
# Seconds to wait for the worker processes to appear before a cancel stops them.
WORKER_STOP_WAIT_SECONDS: Final[float] = 2.0
# Seconds to wait for a stopped worker process to end.
WORKER_JOIN_SECONDS: Final[float] = 2.0
# Seconds between the checks for the worker processes.
WORKER_POLL_SECONDS: Final[float] = 0.05
# The message a file gets when its worker process died.
WORKER_CRASH_MESSAGE: Final[str] = (
    "The analysis worker stopped while reading this file. Check the file and try again."
)
# The message a file gets when the batch was cancelled before its analysis ran.
CANCELLED_MESSAGE: Final[str] = "The analysis was cancelled before this file ran."


class PoolCancelled(Exception):
    """Raised inside the pool when a cancel request stops the workers."""


@dataclass(frozen=True)
class AnalysisOutcome:
    """The outcome of one analysis task."""

    file_id: str
    path: Path
    result: AnalysisResult | None = None
    error: str | None = None
    cancelled: bool = False

    @property
    def failed(self) -> bool:
        """Return True when the task produced no result."""
        return self.result is None


@dataclass
class _Task:
    """One submitted file and the future that analyzes it."""

    file_id: str
    path: Path
    future: Future | None = None
    retried: bool = False
    error: str | None = None


def resolve_worker_count(requested: int | None = None) -> int:
    """Return the number of worker processes the pool may run.

    The requested count comes from the settings. The CPU count caps it, and the settings
    cap of four workers bounds the memory that the decoders hold at once.
    """
    wanted = DEFAULT_ANALYSIS_WORKERS if requested is None else requested
    bounded = max(MIN_ANALYSIS_WORKERS, min(wanted, MAX_ANALYSIS_WORKERS))
    cpu_count = os.cpu_count() or MIN_ANALYSIS_WORKERS
    return max(MIN_ANALYSIS_WORKERS, min(bounded, cpu_count))


class AnalysisPool:
    """Run the local BPM and key analysis on worker processes.

    The pool starts no process before the first submission. A cancel stops the worker
    processes at once, so the analysis ends without waiting for the running files.
    """

    def __init__(
        self,
        analyzer_factory: AnalyzerFactory = LocalDspAnalyzer,
        worker_count: int | None = None,
    ) -> None:
        """Hold the analyzer factory and the worker count."""
        self._analyzer_factory = analyzer_factory
        self._max_workers = resolve_worker_count(worker_count)
        self._executor: ProcessPoolExecutor | None = None
        self._tasks: deque[_Task] = deque()
        self._ready: deque[AnalysisOutcome] = deque()
        # The children that existed before this pool started are not its workers.
        self._other_pids = {process.pid for process in multiprocessing.active_children()}
        self._cancelled = False

    @property
    def max_workers(self) -> int:
        """Return the number of worker processes this pool may run."""
        return self._max_workers

    def submit(self, path: Path, file_id: str) -> None:
        """Queue one file for analysis."""
        if self._cancelled:
            raise RuntimeError("The analysis pool was cancelled and takes no new work.")
        task = _Task(file_id=file_id, path=Path(path))
        self._tasks.append(task)
        self._start(task)

    def next_outcome(self, should_cancel: Callable[[], bool] | None = None) -> AnalysisOutcome:
        """Wait for the next submitted task and return its outcome.

        The outcomes come in submission order. A cancel request stops the workers at once
        and returns an outcome that carries no result.
        """
        if self._ready:
            return self._ready.popleft()
        if not self._tasks:
            raise RuntimeError("No analysis task was submitted to the pool.")
        task = self._tasks[0]
        if task.error is not None:
            self._tasks.popleft()
            return AnalysisOutcome(file_id=task.file_id, path=task.path, error=task.error)
        try:
            result = self._await_result(task, should_cancel)
        except PoolCancelled:
            self._tasks.clear()
            return AnalysisOutcome(
                file_id=task.file_id, path=task.path, error=CANCELLED_MESSAGE, cancelled=True
            )
        except BrokenExecutor as error:
            self._recover_from_crash()
            if not self._ready:
                raise RuntimeError("The analysis pool lost the outcome of a task.") from error
            return self._ready.popleft()
        except Exception as error:
            self._tasks.popleft()
            message = f"{type(error).__name__}: {error}"
            return AnalysisOutcome(file_id=task.file_id, path=task.path, error=message)
        self._tasks.popleft()
        return AnalysisOutcome(file_id=task.file_id, path=task.path, result=result)

    def cancel(self) -> None:
        """Stop the worker processes at once and drop the queued files."""
        self._cancelled = True
        self._tasks.clear()
        self._ready.clear()
        self._shutdown_executor(terminate=True)

    def close(self) -> None:
        """Stop the pool and release the worker processes."""
        self._tasks.clear()
        self._ready.clear()
        self._shutdown_executor()

    def __enter__(self) -> AnalysisPool:
        """Support the context manager for a deterministic release."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the pool when the context block ends."""
        self.close()

    def _start(self, task: _Task) -> None:
        """Send one task to the pool."""
        executor = self._executor
        if executor is None:
            executor = self._new_executor(self._max_workers)
        try:
            task.future = executor.submit(
                analyze_track, str(task.path), task.file_id, self._analyzer_factory
            )
        except Exception as error:
            task.future = None
            task.error = f"{type(error).__name__}: {error}"

    def _await_result(
        self, task: _Task, should_cancel: Callable[[], bool] | None
    ) -> AnalysisResult:
        """Wait for the result of one task. A cancel request stops the workers."""
        future = task.future
        if future is None:
            raise RuntimeError("The analysis task never started.")
        while True:
            # The wait reports whether the task ended. Reading the result of a task that
            # ended keeps a worker error apart from the wait.
            done, _pending = wait({future}, timeout=CANCEL_POLL_SECONDS)
            if future in done:
                return future.result()
            if should_cancel is not None and should_cancel():
                self.cancel()
                raise PoolCancelled from None

    def _recover_from_crash(self) -> None:
        """Rebuild the pool after a worker died and finish the unfinished files.

        A worker that dies takes every task in flight with it. Each unfinished file then
        runs on a single worker of its own, so one bad file cannot stop the others. A file
        that crashes again is reported as failed.
        """
        pending = list(self._tasks)
        self._tasks.clear()
        self._shutdown_executor()
        for task in pending:
            self._ready.append(self._run_alone(task))

    def _run_alone(self, task: _Task) -> AnalysisOutcome:
        """Run one file on a single worker, then retry it once when the worker dies."""
        outcome, crashed = self._run_task(task)
        if not crashed or task.retried:
            return outcome
        task.retried = True
        logger.warning("The analysis worker died on %s. The file runs again.", task.path)
        outcome, _crashed = self._run_task(task)
        return outcome

    def _run_task(self, task: _Task) -> tuple[AnalysisOutcome, bool]:
        """Run one task on a fresh pool. Return the outcome and whether the worker died."""
        executor = self._new_executor(1)
        try:
            future = executor.submit(
                analyze_track, str(task.path), task.file_id, self._analyzer_factory
            )
            result = future.result()
        except BrokenExecutor:
            return AnalysisOutcome(
                file_id=task.file_id, path=task.path, error=WORKER_CRASH_MESSAGE
            ), True
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            return AnalysisOutcome(file_id=task.file_id, path=task.path, error=message), False
        else:
            return AnalysisOutcome(file_id=task.file_id, path=task.path, result=result), False
        finally:
            self._shutdown_executor()

    def _new_executor(self, workers: int) -> ProcessPoolExecutor:
        """Start a pool of worker processes."""
        executor = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
        )
        self._executor = executor
        return executor

    def _shutdown_executor(self, terminate: bool = False) -> None:
        """Close the pool. A terminate stops the worker processes at once."""
        executor, self._executor = self._executor, None
        if executor is None:
            return
        if terminate:
            self._stop_workers()
        executor.shutdown(wait=not terminate, cancel_futures=True)

    def _stop_workers(self) -> None:
        """Stop the worker processes at once so a cancel ends the analysis now."""
        processes = self._worker_processes()
        deadline = time.monotonic() + WORKER_STOP_WAIT_SECONDS
        while not processes and time.monotonic() < deadline:
            time.sleep(WORKER_POLL_SECONDS)
            processes = self._worker_processes()
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=WORKER_JOIN_SECONDS)

    def _worker_processes(self) -> list[multiprocessing.Process]:
        """Return the worker processes that belong to this pool.

        The application starts no other child process, so every child that started after
        this pool belongs to it.
        """
        return [
            process
            for process in multiprocessing.active_children()
            if process.pid not in self._other_pids
        ]
