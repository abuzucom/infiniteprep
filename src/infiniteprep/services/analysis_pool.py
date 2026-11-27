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
# The message a file gets when its analysis took longer than the time limit.
ANALYSIS_TIMEOUT_MESSAGE: Final[str] = (
    "The analysis of this file took longer than the time limit. Check the file."
)
# Seconds one file may take in a worker. The analyzer reads at most 30 minutes of audio,
# so a healthy file ends in seconds. The limit ends a file whose decoder hangs.
ANALYSIS_TIMEOUT_SECONDS: Final[float] = 3600.0
# A file runs at most twice. The second run is the retry after a worker died.
MAX_TASK_ATTEMPTS: Final[int] = 2


class PoolCancelled(Exception):
    """Raised inside the pool when a cancel request stops the workers."""


class AnalysisTimedOut(Exception):
    """Raised inside the pool when one file takes longer than the time limit."""


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
    attempts: int = 0
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
        return self._head_outcome(self._tasks[0], should_cancel)

    def _head_outcome(
        self, task: _Task, should_cancel: Callable[[], bool] | None
    ) -> AnalysisOutcome:
        """Wait for the outcome of the task at the head of the queue."""
        if task.error is not None:
            return self._failed_outcome(task, task.error)
        try:
            result = self._await_result(task, should_cancel)
        except PoolCancelled:
            return self._cancelled_outcome(task)
        except BrokenExecutor as error:
            return self._recover_outcome(task, should_cancel, error)
        except AnalysisTimedOut:
            return self._timed_out_outcome(task)
        except Exception as error:
            logger.exception("The analysis of %s failed in the worker.", task.path)
            return self._failed_outcome(task, f"{type(error).__name__}: {error}")
        self._tasks.popleft()
        return AnalysisOutcome(file_id=task.file_id, path=task.path, result=result)

    def _failed_outcome(self, task: _Task, message: str) -> AnalysisOutcome:
        """Return the outcome of a task that ended with an error."""
        self._tasks.popleft()
        return AnalysisOutcome(file_id=task.file_id, path=task.path, error=message)

    def _cancelled_outcome(self, task: _Task) -> AnalysisOutcome:
        """Return the outcome of a task that a cancel request stopped."""
        self._tasks.clear()
        return AnalysisOutcome(
            file_id=task.file_id, path=task.path, error=CANCELLED_MESSAGE, cancelled=True
        )

    def _timed_out_outcome(self, task: _Task) -> AnalysisOutcome:
        """Return the outcome of a file that took longer than the time limit.

        The worker that hung ends now. The pool rebuilds for the files after it.
        """
        self._shutdown_executor()
        self._tasks.popleft()
        return AnalysisOutcome(file_id=task.file_id, path=task.path, error=ANALYSIS_TIMEOUT_MESSAGE)

    def _recover_outcome(
        self,
        task: _Task,
        should_cancel: Callable[[], bool] | None,
        error: BrokenExecutor,
    ) -> AnalysisOutcome:
        """Rebuild the pool after a crash and return the outcome of the next file."""
        try:
            self._recover_from_crash(should_cancel)
        except PoolCancelled:
            return self._cancelled_outcome(task)
        if not self._ready:
            raise RuntimeError("The analysis pool lost the outcome of a task.") from error
        return self._ready.popleft()

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
        task.attempts += 1
        try:
            task.future = executor.submit(
                analyze_track, str(task.path), task.file_id, self._analyzer_factory
            )
        except Exception as error:
            logger.exception("The analysis pool could not start %s.", task.path)
            task.future = None
            task.error = f"{type(error).__name__}: {error}"

    def _await_result(
        self, task: _Task, should_cancel: Callable[[], bool] | None
    ) -> AnalysisResult:
        """Wait for the result of one task. A cancel request stops the workers."""
        future = task.future
        if future is None:
            raise RuntimeError("The analysis task never started.")
        return self._wait_for(future, task, should_cancel)

    def _wait_for(
        self,
        future: Future,
        task: _Task,
        should_cancel: Callable[[], bool] | None,
    ) -> AnalysisResult:
        """Wait for one worker result. The wait observes a cancel request and a deadline."""
        deadline = time.monotonic() + ANALYSIS_TIMEOUT_SECONDS
        while True:
            # The wait reports whether the task ended. Reading the result of a task that
            # ended keeps a worker error apart from the wait.
            done, _pending = wait({future}, timeout=CANCEL_POLL_SECONDS)
            if future in done:
                return future.result()
            if should_cancel is not None and should_cancel():
                self.cancel()
                raise PoolCancelled from None
            if time.monotonic() >= deadline:
                if self._executor is not None:
                    self._stop_workers(self._executor)
                raise AnalysisTimedOut from None

    def _recover_from_crash(self, should_cancel: Callable[[], bool] | None = None) -> None:
        """Rebuild the pool after a worker died and finish the unfinished files.

        A worker that dies takes every task in flight with it. Each unfinished file then
        runs on a single worker of its own, so one bad file cannot stop the others. A file
        that crashes again is reported as failed. A file that ended before the crash keeps
        its result.
        """
        pending = list(self._tasks)
        self._tasks.clear()
        self._shutdown_executor()
        for task in pending:
            outcome = self._finished_outcome(task)
            if outcome is None:
                outcome = self._run_alone(task, should_cancel)
            self._ready.append(outcome)

    @staticmethod
    def _finished_outcome(task: _Task) -> AnalysisOutcome | None:
        """Return the outcome of a task that ended before the pool broke."""
        future = task.future
        if future is None or not future.done():
            return None
        try:
            result = future.result()
        except BrokenExecutor:
            return None
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            return AnalysisOutcome(file_id=task.file_id, path=task.path, error=message)
        return AnalysisOutcome(file_id=task.file_id, path=task.path, result=result)

    def _run_alone(
        self, task: _Task, should_cancel: Callable[[], bool] | None = None
    ) -> AnalysisOutcome:
        """Run one file on a single worker, then retry it once when the worker dies."""
        outcome, crashed = self._run_task(task, should_cancel)
        if not crashed or task.attempts >= MAX_TASK_ATTEMPTS:
            return outcome
        logger.warning("The analysis worker died on %s. The file runs again.", task.path)
        outcome, _crashed = self._run_task(task, should_cancel)
        return outcome

    def _run_task(
        self, task: _Task, should_cancel: Callable[[], bool] | None = None
    ) -> tuple[AnalysisOutcome, bool]:
        """Run one task on a fresh pool. Return the outcome and whether the worker died."""
        executor = self._new_executor(1)
        task.attempts += 1
        try:
            future = executor.submit(
                analyze_track, str(task.path), task.file_id, self._analyzer_factory
            )
            result = self._wait_for(future, task, should_cancel)
        except BrokenExecutor:
            return AnalysisOutcome(
                file_id=task.file_id, path=task.path, error=WORKER_CRASH_MESSAGE
            ), True
        except PoolCancelled:
            # A cancel request ends the pool. It is not a failure of one file.
            raise
        except AnalysisTimedOut:
            # The worker hung. The pool rebuilds and the file ends as failed.
            self._shutdown_executor()
            return AnalysisOutcome(
                file_id=task.file_id, path=task.path, error=ANALYSIS_TIMEOUT_MESSAGE
            ), False
        except Exception as error:
            logger.exception("The analysis of %s failed in the worker.", task.path)
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
            self._stop_workers(executor)
        executor.shutdown(wait=not terminate, cancel_futures=True)

    def _stop_workers(self, executor: ProcessPoolExecutor) -> None:
        """Stop the worker processes at once so a cancel ends the analysis now."""
        processes = self._worker_processes(executor)
        deadline = time.monotonic() + WORKER_STOP_WAIT_SECONDS
        while not processes and time.monotonic() < deadline:
            time.sleep(WORKER_POLL_SECONDS)
            processes = self._worker_processes(executor)
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=WORKER_JOIN_SECONDS)

    @staticmethod
    def _worker_processes(executor: ProcessPoolExecutor) -> list[multiprocessing.Process]:
        """Return the worker processes that the executor owns.

        The executor keeps the processes that it started. That map names the workers of
        this pool exactly, so a child process of another component stays untouched.
        """
        processes = getattr(executor, "_processes", None)
        if not isinstance(processes, dict):
            return []
        return [process for process in processes.values() if process.is_alive()]
