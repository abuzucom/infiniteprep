"""Behavioral test: a queue state change before the drop sync keeps the dragged order."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QWidget

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind
from infiniteprep.ui.batch_queue import BatchQueueController
from infiniteprep.ui.batch_queue_dock import BatchQueueDock
from infiniteprep.ui.folder_import import FolderImportController
from tests.fixtures.batch_jobs import GatedJobs

SIGNAL_TIMEOUT_MS = 10_000
DRAGGED_ROW = 2
DROP_ROW = 1


def test_a_state_change_before_the_drop_sync_keeps_the_dragged_order(
    qtbot: Any, tmp_path: Path
) -> None:
    """The dock applies a dropped order even when the queue announces a change first.

    The drop sync runs on the next event loop turn. A rebuild of the rows before that
    turn would put them back in queue order and lose the drop.
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
    first = controller.enqueue_job(BatchKind.ANALYZE, "first", jobs.make("first"))
    second = controller.enqueue_job(BatchKind.ANALYZE, "second", jobs.make("second"))
    third = controller.enqueue_job(BatchKind.ANALYZE, "third", jobs.make("third"))

    try:
        model = dock.batch_list.model()
        assert model.moveRow(QModelIndex(), DRAGGED_ROW, QModelIndex(), DROP_ROW)
        controller.changed.emit()

        assert dock.listed_batch_ids() == [first.id, third.id, second.id]
        qtbot.waitUntil(
            lambda: controller.queue.waiting() == [third, second], timeout=SIGNAL_TIMEOUT_MS
        )
        assert dock.listed_batch_ids() == [first.id, third.id, second.id]
    finally:
        # The gate opens on every path, so no worker thread outlives the test.
        jobs.gate.set()
        qtbot.waitUntil(lambda: not controller.is_busy(), timeout=SIGNAL_TIMEOUT_MS)

    assert jobs.log == ["first", "third", "second"]
