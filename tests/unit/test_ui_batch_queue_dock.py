"""Behavioral tests for the batch queue controller and its dock: order, reorder, cancel."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QWidget

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind, BatchState, QueuedBatch
from infiniteprep.services.write_service import WriteBatchResult
from infiniteprep.ui.batch_queue import (
    MAX_LISTED_FAILURES,
    BatchQueueController,
    BatchRun,
    format_batch_result,
)
from infiniteprep.ui.batch_queue_dock import BatchQueueDock
from infiniteprep.ui.folder_import import FolderImportController
from tests.fixtures.batch_jobs import GatedJobs

SIGNAL_TIMEOUT_MS = 10_000

JobFactory = Callable[[str], BatchRun]
DockParts = tuple[BatchQueueController, BatchQueueDock, JobFactory, list[str], threading.Event]


@pytest.fixture
def dock_parts(qtbot: Any, tmp_path: Path) -> Iterator[DockParts]:
    """Build a controller, its dock, and a factory of jobs that wait on one shared gate.

    Each job appends its name to the log when it starts. Teardown opens the gate and
    waits for the queue, so no worker thread outlives its test.
    """
    parent = QWidget()
    qtbot.addWidget(parent)
    repository = Repository(DatabaseManager(tmp_path / "queue.sqlite3"))
    import_controller = FolderImportController(
        ImportService(repository), parent, show_progress_dialog=False
    )
    controller = BatchQueueController(import_controller, repository.db.close, parent)
    dock = BatchQueueDock(controller)
    qtbot.addWidget(dock)
    jobs = GatedJobs()

    yield controller, dock, jobs.make, jobs.log, jobs.gate
    jobs.gate.set()
    qtbot.waitUntil(lambda: not controller.is_busy(), timeout=SIGNAL_TIMEOUT_MS)


def enqueue_three(parts: DockParts) -> tuple[QueuedBatch, QueuedBatch, QueuedBatch]:
    """Queue three gated jobs named first, second, and third."""
    controller, _dock, make_job, _log, _gate = parts
    return (
        controller.enqueue_job(BatchKind.ANALYZE, "first", make_job("first")),
        controller.enqueue_job(BatchKind.WRITE, "second", make_job("second")),
        controller.enqueue_job(BatchKind.ANALYZE, "third", make_job("third")),
    )


def test_one_batch_runs_at_a_time_in_arrival_order(qtbot: Any, dock_parts: DockParts) -> None:
    """The first batch runs while the others wait. All three then run in FIFO order."""
    controller, dock, _make_job, log, gate = dock_parts
    first, second, third = enqueue_three(dock_parts)

    qtbot.waitUntil(lambda: log == ["first"], timeout=SIGNAL_TIMEOUT_MS)
    assert first.state is BatchState.RUNNING
    assert [second.state, third.state] == [BatchState.WAITING, BatchState.WAITING]
    assert dock.listed_batch_ids() == [first.id, second.id, third.id]

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        gate.set()

    assert log == ["first", "second", "third"]
    assert {first.state, second.state, third.state} == {BatchState.COMPLETED}
    assert not controller.is_busy()


def test_move_buttons_reorder_waiting_batches(qtbot: Any, dock_parts: DockParts) -> None:
    """Move Up puts the last waiting batch ahead. The running batch offers no move."""
    controller, dock, _make_job, log, gate = dock_parts
    first, second, third = enqueue_three(dock_parts)

    assert dock.select_batch(first.id)
    assert not dock.move_up_btn.isEnabled()
    assert not dock.move_down_btn.isEnabled()

    assert dock.select_batch(third.id)
    dock.move_up_btn.click()
    assert dock.listed_batch_ids() == [first.id, third.id, second.id]
    assert dock.selected_batch() is third
    dock.move_down_btn.click()
    dock.move_up_btn.click()
    assert controller.queue.waiting() == [third, second]

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        gate.set()

    assert log == ["first", "third", "second"]


def test_dragging_a_row_reorders_waiting_batches(qtbot: Any, dock_parts: DockParts) -> None:
    """A row move in the list becomes the queue order. A move past the running batch reverts."""
    controller, dock, _make_job, log, gate = dock_parts
    first, second, third = enqueue_three(dock_parts)
    model = dock.batch_list.model()

    assert model.moveRow(QModelIndex(), 2, QModelIndex(), 1)
    qtbot.waitUntil(
        lambda: controller.queue.waiting() == [third, second], timeout=SIGNAL_TIMEOUT_MS
    )
    assert dock.listed_batch_ids() == [first.id, third.id, second.id]

    assert model.moveRow(QModelIndex(), 1, QModelIndex(), 0)
    qtbot.waitUntil(
        lambda: dock.listed_batch_ids() == [first.id, third.id, second.id],
        timeout=SIGNAL_TIMEOUT_MS,
    )
    assert controller.queue.running is first

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        gate.set()

    assert log == ["first", "third", "second"]


def test_cancel_removes_a_waiting_batch(qtbot: Any, dock_parts: DockParts) -> None:
    """A cancelled waiting batch never starts and stays listed as cancelled."""
    controller, dock, _make_job, log, gate = dock_parts
    first, second, third = enqueue_three(dock_parts)

    assert dock.select_batch(second.id)
    dock.cancel_btn.click()

    assert second.state is BatchState.CANCELLED
    assert controller.queue.waiting() == [third]
    assert dock.listed_batch_ids() == [first.id, third.id, second.id]

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        gate.set()

    assert log == ["first", "third"]


def test_cancel_stops_the_running_batch_and_the_next_one_starts(
    qtbot: Any, dock_parts: DockParts
) -> None:
    """The running batch sees the cancel request, ends as cancelled, and the queue goes on."""
    controller, dock, make_job, log, gate = dock_parts
    first = controller.enqueue_job(BatchKind.ANALYZE, "first", make_job("first"))
    second = controller.enqueue_job(BatchKind.ANALYZE, "second", make_job("second"))
    qtbot.waitUntil(lambda: log == ["first"], timeout=SIGNAL_TIMEOUT_MS)

    assert dock.select_batch(first.id)
    dock.cancel_btn.click()
    assert first.cancel_requested
    assert not dock.cancel_btn.isEnabled()

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        gate.set()

    assert first.state is BatchState.CANCELLED
    assert second.state is BatchState.COMPLETED
    assert log == ["first", "second"]


def test_a_batch_that_raises_is_failed_and_the_next_one_starts(
    qtbot: Any, dock_parts: DockParts
) -> None:
    """An error in a batch body fails that batch only. Its text reaches the result."""
    controller, _dock, make_job, log, gate = dock_parts
    gate.set()

    def explode(_should_cancel: Any, _progress: Any) -> Any:
        raise RuntimeError("boom")

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        failed = controller.enqueue_job(BatchKind.WRITE, "broken", explode)
        after = controller.enqueue_job(BatchKind.ANALYZE, "after", make_job("after"))

    assert failed.state is BatchState.FAILED
    assert after.state is BatchState.COMPLETED
    assert log == ["after"]
    assert "RuntimeError: boom" in format_batch_result(failed)


def test_rows_show_progress_and_finished_batches_offer_their_result(
    qtbot: Any, dock_parts: DockParts
) -> None:
    """A running row shows its progress. View Result names the batch. Clear empties the list."""
    controller, dock, make_job, _log, gate = dock_parts
    first = controller.enqueue_job(BatchKind.ANALYZE, "first", make_job("first"))

    qtbot.waitUntil(
        lambda: dock.batch_list.item(0).text() == "first: Running. 1 of 1: first",
        timeout=SIGNAL_TIMEOUT_MS,
    )
    assert dock.select_batch(first.id)
    assert not dock.view_result_btn.isEnabled()
    assert not dock.clear_finished_btn.isEnabled()

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        gate.set()

    assert dock.batch_list.item(0).text() == "first: Completed. Processed: 1. Failed: 0."
    assert dock.selected_batch() is first
    with qtbot.waitSignal(dock.result_requested, timeout=SIGNAL_TIMEOUT_MS) as requested:
        dock.view_result_btn.click()
    assert requested.args[0] is first

    dock.clear_finished_btn.click()
    assert dock.listed_batch_ids() == []


def test_result_text_lists_counts_and_caps_the_failed_files() -> None:
    """The result names the counts and the first failed files, then counts the rest."""
    extra = 3
    result = WriteBatchResult(
        failures=[(f"/music/track{n}.wav", "OSError: disk full") for n in range(13)]
    )
    batch = QueuedBatch(id="b1", kind=BatchKind.WRITE, label="Apply writes", result=result)
    batch.state = BatchState.COMPLETED

    text = format_batch_result(batch)

    assert text.splitlines()[0] == "Apply writes: Completed"
    assert "Written: 0. Failed: 13." in text
    assert "/music/track0.wav: OSError: disk full" in text
    assert f"/music/track{MAX_LISTED_FAILURES}.wav" not in text
    assert f"{extra} more failed files are not listed." in text
