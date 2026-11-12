"""Batch bodies for queue tests that start, report progress, and wait on a shared gate."""

from __future__ import annotations

import threading

from infiniteprep.services.job_orchestrator import BatchJobSummary
from infiniteprep.ui.batch_queue import BatchRun, CancelCheck, ProgressReport

GATE_TIMEOUT_SEC = 10.0


class GatedJobs:
    """Make batch bodies that log their start and block until the test opens the gate."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.log: list[str] = []

    def make(self, name: str) -> BatchRun:
        """Return a body that logs name, reports one file, waits, and honors a cancel."""

        def run(should_cancel: CancelCheck, progress: ProgressReport) -> BatchJobSummary:
            self.log.append(name)
            progress(1, 1, name)
            self.gate.wait(timeout=GATE_TIMEOUT_SEC)
            return BatchJobSummary(completed_files=1, stopped_early=should_cancel())

        return run
