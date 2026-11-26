"""Tests for the Batch Queue strip's time-left pace and its per-batch bookkeeping.

These tests drive the controller's queue state directly. No worker thread starts.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QWidget

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind, BatchState, QueuedBatch
from infiniteprep.ui.batch_queue import BatchQueueController
from infiniteprep.ui.batch_queue_strip import BatchQueueStrip
from infiniteprep.ui.folder_import import FolderImportController

START_TIME = 100.0
SECONDS_PER_FILE = 30.0
TOTAL_FILES = 4


class StepClock:
    """A clock that returns a set time, so the time-left estimate is exact."""

    def __init__(self) -> None:
        """Start at START_TIME."""
        self.now = START_TIME

    def __call__(self) -> float:
        """Return the current set time."""
        return self.now


StripParts = tuple[BatchQueueController, BatchQueueStrip, StepClock]


@pytest.fixture
def strip_parts(qtbot: Any, tmp_path: Path) -> Iterator[StripParts]:
    """Build a controller and a strip with a set clock."""
    parent = QWidget()
    qtbot.addWidget(parent)
    repository = Repository(DatabaseManager(tmp_path / "queue.sqlite3"))
    import_controller = FolderImportController(
        ImportService(repository), parent, show_progress_dialog=False
    )
    controller = BatchQueueController(import_controller, repository.db.close, parent)
    clock = StepClock()
    strip = BatchQueueStrip(controller, parent, clock=clock)
    yield controller, strip, clock
    repository.db.close()


def start_batch(controller: BatchQueueController) -> QueuedBatch:
    """Mark a new Analyze batch as running without starting its worker."""
    controller.queue.enqueue(BatchKind.ANALYZE, "Analyze")
    batch = controller.queue.start_next()
    assert batch is not None
    controller.changed.emit()
    return batch


def report(controller: BatchQueueController, done: int, path: str) -> None:
    """Report worker progress of done files out of TOTAL_FILES."""
    controller._show_progress(False, done, TOTAL_FILES, path)


def test_estimate_ignores_current_file_time(strip_parts: StripParts) -> None:
    """Time spent on the file in progress leaves the pace of the finished files unchanged."""
    controller, strip, clock = strip_parts
    start_batch(controller)
    report(controller, 0, "/music/a.wav")
    clock.now = START_TIME + 2 * SECONDS_PER_FILE
    report(controller, 2, "/music/c.wav")
    at_second_file = strip.time_label.text()

    clock.now = START_TIME + 4 * SECONDS_PER_FILE
    report(controller, 2, "/music/c.wav")

    assert at_second_file == "1 min left"
    assert strip.time_label.text() == "1 min left"


def test_finished_batch_clears_start_time(strip_parts: StripParts) -> None:
    """A finished batch drops out of the strip's start times."""
    controller, strip, _clock = strip_parts
    batch = start_batch(controller)
    report(controller, 1, "/music/a.wav")

    controller.queue.finish_running(BatchState.COMPLETED)
    controller.changed.emit()

    assert batch.id not in strip._started_at
    assert batch.id not in strip._last_finish
    assert not strip.isVisible()
