"""Time limit per file for background batches, with a Quit Now or Keep Waiting prompt."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QObject, Qt, QTimer, Slot
from PySide6.QtWidgets import QAbstractButton, QMessageBox, QWidget

logger = logging.getLogger(__name__)

QUIT_NOW_TEXT: Final[str] = "Quit Now"
KEEP_WAITING_TEXT: Final[str] = "Keep Waiting"


@dataclass(frozen=True)
class StuckPrompt:
    """The time limit and the prompt wording for one kind of batch."""

    title: str
    text: str
    timeout_ms: int


class StuckFileWatcher(QObject):
    """Raise a Quit Now or Keep Waiting prompt when one file runs past its time limit.

    The owner calls start when a batch starts and again before each file, and stop
    when the batch ends. Quit Now calls exit_process, which ends the process at once.
    Keep Waiting closes the prompt and restarts the full limit.
    """

    def __init__(
        self,
        parent_widget: QWidget | None,
        prompt: StuckPrompt,
        is_running: Callable[[], bool],
        *,
        exit_code: int,
        exit_process: Callable[[int], object] = os._exit,
        owner: QObject | None = None,
    ) -> None:
        """Keep the prompt wording, the running check, and the exit call and code."""
        super().__init__(owner)
        self.parent_widget = parent_widget
        self.prompt = prompt
        self._is_running = is_running
        self._exit_code = exit_code
        self._exit_process = exit_process
        self.stuck_box: QMessageBox | None = None
        self.quit_now_button: QAbstractButton | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(prompt.timeout_ms)
        self.timer.timeout.connect(self._on_file_stuck)

    def use_prompt(self, prompt: StuckPrompt) -> None:
        """Switch the wording and the limit for the next start."""
        self.prompt = prompt
        self.timer.setInterval(prompt.timeout_ms)

    def start(self) -> None:
        """Start or restart the full time limit for the current file."""
        self.timer.start()

    def stop(self) -> None:
        """Stop the time limit and close the prompt if it is open."""
        self.timer.stop()
        self._close_stuck_box()

    @Slot()
    def _on_file_stuck(self) -> None:
        """Offer Quit Now or Keep Waiting after one file runs past the time limit."""
        if not self._is_running() or self.stuck_box is not None:
            return
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            self.prompt.title,
            self.prompt.text,
            QMessageBox.StandardButton.NoButton,
            self.parent_widget,
        )
        box.setTextFormat(Qt.TextFormat.PlainText)
        self.quit_now_button = box.addButton(QUIT_NOW_TEXT, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(KEEP_WAITING_TEXT, QMessageBox.ButtonRole.RejectRole)
        box.buttonClicked.connect(self._on_stuck_choice)
        self.stuck_box = box
        box.open()

    @Slot(QAbstractButton)
    def _on_stuck_choice(self, button: QAbstractButton) -> None:
        """End the process at once, or wait another full time limit."""
        quit_now = button is self.quit_now_button
        self._close_stuck_box()
        if quit_now:
            logger.warning("%s: Quit Now ends the process.", self.prompt.title)
            self._exit_process(self._exit_code)
            return
        if self._is_running():
            self.timer.start()

    def _close_stuck_box(self) -> None:
        """Close the stuck prompt if it is open."""
        box, self.stuck_box = self.stuck_box, None
        if box is not None:
            box.close()
            box.deleteLater()
