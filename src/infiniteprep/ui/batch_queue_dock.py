"""Dock panel that lists the running, waiting, and finished batches with their controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.services.job_orchestrator import FINISHED_STATES, BatchState, QueuedBatch
from infiniteprep.ui.batch_queue import BatchQueueController, format_batch_row

MOVE_UP_OFFSET = -1
MOVE_DOWN_OFFSET = 1


def _can_cancel(batch: QueuedBatch | None) -> bool:
    """Return True for a waiting batch, or a running batch with no cancel pending."""
    if batch is None:
        return False
    if batch.state is BatchState.WAITING:
        return True
    return batch.state is BatchState.RUNNING and not batch.cancel_requested


class BatchQueueDock(QDockWidget):
    """Show every batch in queue order and offer reorder, cancel, and result controls.

    Waiting batches reorder through the Move buttons or by drag and drop. The running
    batch and finished batches never move. result_requested carries the QueuedBatch
    whose result the person asked to see.
    """

    result_requested = Signal(object)

    def __init__(self, controller: BatchQueueController, parent: QWidget | None = None) -> None:
        """Build the list and its buttons, and follow the controller's state changes."""
        super().__init__("Batch Queue", parent)
        self.setObjectName("batch_queue_dock")
        self.controller = controller
        self._refreshing = False
        self.batch_list = QListWidget(self)
        self.move_up_btn = QPushButton("Move Up", self)
        self.move_down_btn = QPushButton("Move Down", self)
        self.cancel_btn = QPushButton("Cancel Batch", self)
        self.view_result_btn = QPushButton("View Result", self)
        self.clear_finished_btn = QPushButton("Clear Finished", self)
        self._build_layout()
        self._connect_signals()
        self.refresh()

    def _build_layout(self) -> None:
        """Place the list above one row of buttons."""
        self.batch_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.batch_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        body = QWidget(self)
        root = QVBoxLayout(body)
        root.addWidget(self.batch_list)
        buttons = QHBoxLayout()
        for button in (
            self.move_up_btn,
            self.move_down_btn,
            self.cancel_btn,
            self.view_result_btn,
            self.clear_finished_btn,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        root.addLayout(buttons)
        self.setWidget(body)

    def _connect_signals(self) -> None:
        """Wire the buttons, the selection, the drag signals, and the controller."""
        self.move_up_btn.clicked.connect(self._move_selected_up)
        self.move_down_btn.clicked.connect(self._move_selected_down)
        self.cancel_btn.clicked.connect(self._cancel_selected)
        self.view_result_btn.clicked.connect(self._request_selected_result)
        self.clear_finished_btn.clicked.connect(self.controller.clear_finished)
        self.batch_list.itemSelectionChanged.connect(self._update_buttons)
        # A drop moves rows on some Qt versions and inserts then removes them on others.
        model = self.batch_list.model()
        model.rowsMoved.connect(self._schedule_drag_sync)
        model.rowsInserted.connect(self._schedule_drag_sync)
        self.controller.changed.connect(self.refresh)
        self.controller.progress_changed.connect(self._on_progress_changed)

    def listed_batch_ids(self) -> list[str]:
        """Return the batch ID of every row, top to bottom."""
        return [
            str(self.batch_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.batch_list.count())
        ]

    def selected_batch(self) -> QueuedBatch | None:
        """Return the batch of the selected row, or None without a selection."""
        item = self.batch_list.currentItem()
        if item is None:
            return None
        batch_id = item.data(Qt.ItemDataRole.UserRole)
        for batch in self.controller.queue.batches():
            if batch.id == batch_id:
                return batch
        return None

    def select_batch(self, batch_id: str) -> bool:
        """Select the row of batch_id. Return False when no row shows it."""
        for row, listed_id in enumerate(self.listed_batch_ids()):
            if listed_id == batch_id:
                self.batch_list.setCurrentRow(row)
                return True
        return False

    def refresh(self) -> None:
        """Rebuild the rows from the queue and keep the selection on the same batch."""
        selected = self.selected_batch()
        self._refreshing = True
        self.batch_list.clear()
        for batch in self.controller.queue.batches():
            item = QListWidgetItem(format_batch_row(batch))
            item.setData(Qt.ItemDataRole.UserRole, batch.id)
            flags = item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled
            if batch.state is not BatchState.WAITING:
                flags &= ~Qt.ItemFlag.ItemIsDragEnabled
            item.setFlags(flags)
            self.batch_list.addItem(item)
        self._refreshing = False
        if selected is not None:
            self.select_batch(selected.id)
        self._update_buttons()

    def _on_progress_changed(self, batch: QueuedBatch) -> None:
        """Update the text of one row without rebuilding the list."""
        for row, listed_id in enumerate(self.listed_batch_ids()):
            if listed_id == batch.id:
                self.batch_list.item(row).setText(format_batch_row(batch))
                return

    def _update_buttons(self) -> None:
        """Enable each button only when it applies to the selected batch."""
        batch = self.selected_batch()
        state = batch.state if batch is not None else None
        is_waiting = state is BatchState.WAITING
        self.move_up_btn.setEnabled(is_waiting)
        self.move_down_btn.setEnabled(is_waiting)
        self.cancel_btn.setEnabled(_can_cancel(batch))
        self.view_result_btn.setEnabled(state in FINISHED_STATES)
        self.clear_finished_btn.setEnabled(bool(self.controller.queue.finished()))

    def _move_selected(self, offset: int) -> None:
        """Shift the selected waiting batch by offset places."""
        batch = self.selected_batch()
        if batch is not None:
            self.controller.move(batch.id, offset)

    def _move_selected_up(self) -> None:
        """Move the selected waiting batch one place earlier."""
        self._move_selected(MOVE_UP_OFFSET)

    def _move_selected_down(self) -> None:
        """Move the selected waiting batch one place later."""
        self._move_selected(MOVE_DOWN_OFFSET)

    def _cancel_selected(self) -> None:
        """Cancel the selected waiting or running batch."""
        batch = self.selected_batch()
        if batch is not None:
            self.controller.cancel(batch.id)

    def _request_selected_result(self) -> None:
        """Ask the owner of this dock to show the selected finished batch."""
        batch = self.selected_batch()
        if batch is not None and batch.state in FINISHED_STATES:
            self.result_requested.emit(batch)

    def _schedule_drag_sync(self, *_args: object) -> None:
        """After a drop settles, copy the dragged order into the queue."""
        if not self._refreshing:
            QTimer.singleShot(0, self._apply_dragged_order)

    def _apply_dragged_order(self) -> None:
        """Send the waiting batches in their listed order to the queue."""
        waiting_ids = {batch.id for batch in self.controller.queue.waiting()}
        dragged_order = [
            batch_id for batch_id in self.listed_batch_ids() if batch_id in waiting_ids
        ]
        self.controller.reorder_waiting(dragged_order)
