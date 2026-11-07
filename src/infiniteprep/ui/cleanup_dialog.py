"""The Settings dialog that deletes InfinitePrep's own data on request (D83a3b)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.data_cleanup import CleanupChoice, CleanupReport

CLEANUP_TITLE: Final[str] = "Delete InfinitePrep Data"
CLEANUP_INTRO: Final[str] = (
    "Choose what to delete. InfinitePrep then closes. Music files and exports stay."
)
DATA_BOX_LABEL: Final[str] = (
    "Delete the library, settings, recovery backups, waveform cache, and downloaded fpcalc"
)
KEYS_BOX_LABEL: Final[str] = "Delete the saved API keys from the system keyring"
FOLDERS_HEADING: Final[str] = "The data box deletes these folders:"
DELETE_LABEL: Final[str] = "Delete..."
CANCEL_LABEL: Final[str] = "Cancel"
CONFIRM_TITLE: Final[str] = "Delete This Data?"
CONFIRM_TEMPLATE: Final[str] = (
    "InfinitePrep will delete:\n{items}\n\nThis cannot be undone. InfinitePrep closes afterward."
)
CONFIRM_KEYS_ITEM: Final[str] = "- the saved API keys"
RESULT_TITLE: Final[str] = "InfinitePrep Data Deleted"
RESULT_FAILED_TITLE: Final[str] = "Some InfinitePrep Data Remains"
BLOCKED_TITLE: Final[str] = "Nothing Was Deleted"
BLOCKED_TEXT: Final[str] = (
    "InfinitePrep stayed open, so nothing was deleted. "
    "Wait for the import to finish, then choose Delete InfinitePrep Data again."
)
DIALOG_MIN_WIDTH: Final[int] = 560

Confirm = Callable[[QMessageBox], bool]


def build_cleanup_confirm(choice: CleanupChoice, folders: list[Path]) -> QMessageBox:
    """Return the final yes/no prompt. No is the default, and the text stays plain (D296)."""
    items = [f"- {folder}" for folder in folders] if choice.delete_data else []
    if choice.delete_keys:
        items.append(CONFIRM_KEYS_ITEM)
    box = QMessageBox(QMessageBox.Icon.Warning, CONFIRM_TITLE, "")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(CONFIRM_TEMPLATE.format(items="\n".join(items)))
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box


def run_confirm_box(box: QMessageBox) -> bool:
    """Show the prompt and return True only when Yes was chosen."""
    box.exec()
    return box.clickedButton() is box.button(QMessageBox.StandardButton.Yes)


def build_cleanup_blocked_box() -> QMessageBox:
    """Return the message shown when the main window stayed open, so nothing was deleted."""
    box = QMessageBox(QMessageBox.Icon.Information, BLOCKED_TITLE, "")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(BLOCKED_TEXT)
    return box


def build_cleanup_result_box(report: CleanupReport) -> QMessageBox:
    """Return the closing message that lists what was removed and what remains."""
    if report.succeeded:
        box = QMessageBox(QMessageBox.Icon.Information, RESULT_TITLE, "")
    else:
        box = QMessageBox(QMessageBox.Icon.Warning, RESULT_FAILED_TITLE, "")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(report.describe())
    return box


class CleanupDialog(QDialog):
    """Two unchecked boxes, the folder list, and a Delete button that confirms first."""

    def __init__(
        self,
        folders: list[Path],
        parent: QWidget | None = None,
        confirm: Confirm = run_confirm_box,
    ) -> None:
        """Build the dialog for these folders. Both boxes start unchecked (D294)."""
        super().__init__(parent)
        self.folders = folders
        self._confirm = confirm
        self.setWindowTitle(CLEANUP_TITLE)
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.data_box = QCheckBox(DATA_BOX_LABEL, self)
        self.keys_box = QCheckBox(KEYS_BOX_LABEL, self)
        self.folder_label = QLabel(self._folder_text(), self)
        self.delete_btn = QPushButton(DELETE_LABEL, self)
        self.cancel_btn = QPushButton(CANCEL_LABEL, self)
        self._build_layout()
        self._update_delete_button()

    def _folder_text(self) -> str:
        """Return the heading and one folder per line."""
        return "\n".join([FOLDERS_HEADING, *(str(folder) for folder in self.folders)])

    def _build_layout(self) -> None:
        """Lay out the intro, the boxes, the folder list, and the buttons."""
        root = QVBoxLayout(self)
        intro = QLabel(CLEANUP_INTRO, self)
        intro.setWordWrap(True)
        root.addWidget(intro)
        root.addWidget(self.data_box)
        self.folder_label.setTextFormat(Qt.TextFormat.PlainText)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.folder_label)
        root.addWidget(self.keys_box)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)
        self.cancel_btn.setDefault(True)
        self.data_box.toggled.connect(self._update_delete_button)
        self.keys_box.toggled.connect(self._update_delete_button)
        self.delete_btn.clicked.connect(self._confirm_and_accept)
        self.cancel_btn.clicked.connect(self.reject)

    def choice(self) -> CleanupChoice:
        """Return the boxes as a choice."""
        return CleanupChoice(
            delete_data=self.data_box.isChecked(), delete_keys=self.keys_box.isChecked()
        )

    def _update_delete_button(self) -> None:
        """Enable Delete only while at least one box is checked."""
        self.delete_btn.setEnabled(self.choice().any_selected)

    def _confirm_and_accept(self) -> None:
        """Ask the final question, and close with Accepted only on Yes."""
        choice = self.choice()
        if not choice.any_selected:
            return
        if self._confirm(build_cleanup_confirm(choice, self.folders)):
            self.accept()
