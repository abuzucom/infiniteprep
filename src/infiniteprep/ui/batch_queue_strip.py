"""The one-line Batch Queue strip at the bottom of the main window."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QToolButton, QWidget

from infiniteprep.services.job_orchestrator import QueuedBatch
from infiniteprep.ui.batch_queue import BatchQueueController
from infiniteprep.ui.time_format import split_time_seconds

STRIP_NAME: Final[str] = "Batch queue progress"
SHOW_QUEUE_LABEL: Final[str] = "Show queue"
HIDE_QUEUE_LABEL: Final[str] = "Hide queue"
ESTIMATING_TEXT: Final[str] = "Estimating time left"
PROGRESS_BAR_WIDTH: Final[int] = 160
STRIP_SIDE_MARGIN: Final[int] = 8
STRIP_END_MARGIN: Final[int] = 2


def format_time_left(seconds: float) -> str:
    """Return a short time-left text such as "2 min left" or "40 s left"."""
    hours, minutes, secs = split_time_seconds(max(0, round(seconds)))
    if hours:
        return f"{hours} h {minutes} min left"
    if minutes:
        return f"{minutes} min left"
    return f"{secs} s left"


def estimate_seconds_left(elapsed: float, done: int, total: int) -> float | None:
    """Return the seconds left at the pace so far, or None before the first file ends."""
    if done <= 0 or total <= done or elapsed <= 0:
        return None
    return elapsed / done * (total - done)


class BatchQueueStrip(QWidget):
    """Show the running batch, its progress, the done-of-total count, and the time left.

    The strip hides while no batch runs or waits. A click on the strip, or on its
    queue button, shows or hides the full queue.
    """

    queue_toggled = Signal(bool)

    def __init__(
        self,
        controller: BatchQueueController,
        parent: QWidget | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build the strip and follow the controller's state and progress."""
        super().__init__(parent)
        self.controller = controller
        self._clock = clock
        self._started_at: dict[str, float] = {}
        self.setAccessibleName(STRIP_NAME)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batch_label = QLabel(self)
        self.batch_label.setTextFormat(Qt.TextFormat.PlainText)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedWidth(PROGRESS_BAR_WIDTH)
        self.progress_bar.setTextVisible(False)
        self.count_label = QLabel(self)
        self.time_label = QLabel(self)
        self.queue_button = QToolButton(self)
        self.queue_button.setCheckable(True)
        self.queue_button.setText(SHOW_QUEUE_LABEL)
        self.queue_button.setAccessibleName(SHOW_QUEUE_LABEL)
        self.queue_button.toggled.connect(self._on_queue_button_toggled)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            STRIP_SIDE_MARGIN, STRIP_END_MARGIN, STRIP_SIDE_MARGIN, STRIP_END_MARGIN
        )
        layout.addWidget(self.batch_label, stretch=1)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.count_label)
        layout.addWidget(self.time_label)
        layout.addWidget(self.queue_button)
        controller.changed.connect(self.refresh)
        controller.progress_changed.connect(self._on_progress_changed)
        self.refresh()

    def refresh(self) -> None:
        """Show the running batch, or hide the strip while nothing runs or waits."""
        running = self.controller.queue.running
        self.setVisible(self.controller.is_busy())
        if running is None:
            self.batch_label.setText("")
            self.progress_bar.setRange(0, 0)
            self.count_label.setText("")
            self.time_label.setText("")
            return
        self._started_at.setdefault(running.id, self._clock())
        self._show_batch(running)

    def set_queue_shown(self, shown: bool) -> None:
        """Mark the queue as shown or hidden without emitting queue_toggled."""
        self.queue_button.blockSignals(True)
        self.queue_button.setChecked(shown)
        self.queue_button.blockSignals(False)
        self._label_queue_button(shown)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Toggle the full queue on a left click anywhere on the strip."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.queue_button.toggle()
        super().mouseReleaseEvent(event)

    def _on_progress_changed(self, batch: QueuedBatch) -> None:
        """Show new progress of the running batch."""
        if batch is self.controller.queue.running:
            self._started_at.setdefault(batch.id, self._clock())
            self._show_batch(batch)

    def _show_batch(self, batch: QueuedBatch) -> None:
        """Show one batch's label, current file, bar, count, and time left."""
        current = Path(batch.progress_path).name if batch.progress_path else ""
        self.batch_label.setText(f"{batch.label}: {current}" if current else batch.label)
        total = batch.progress_total
        if total <= 0:
            # Before the first report the length is unknown, so the bar runs indeterminate.
            self.progress_bar.setRange(0, 0)
            self.count_label.setText("")
            self.time_label.setText("")
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(batch.progress_done)
        self.count_label.setText(f"{batch.progress_done} of {total}")
        elapsed = self._clock() - self._started_at[batch.id]
        left = estimate_seconds_left(elapsed, batch.progress_done, total)
        self.time_label.setText(ESTIMATING_TEXT if left is None else format_time_left(left))

    def _on_queue_button_toggled(self, shown: bool) -> None:
        """Relabel the queue button and report the new queue state."""
        self._label_queue_button(shown)
        self.queue_toggled.emit(shown)

    def _label_queue_button(self, shown: bool) -> None:
        """Name the queue button for the action it takes next."""
        label = HIDE_QUEUE_LABEL if shown else SHOW_QUEUE_LABEL
        self.queue_button.setText(label)
        self.queue_button.setAccessibleName(label)
