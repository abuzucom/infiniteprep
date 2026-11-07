"""The startup prompt for a portable folder whose Data folder cannot be written."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QMessageBox

from infiniteprep.storage_paths import FallbackAnswer

FALLBACK_TITLE: Final[str] = "Portable Folder Is Read-Only"
FALLBACK_TEXT_TEMPLATE: Final[str] = (
    "InfinitePrep cannot write its Data folder in {folder}. "
    "Use my user profile keeps this session's library in your user folders instead. "
    "Quit closes InfinitePrep. Move the folder somewhere writable to use portable mode."
)
USE_PER_USER_LABEL: Final[str] = "Use my user profile"
QUIT_LABEL: Final[str] = "Quit"
REMEMBER_LABEL: Final[str] = "Remember this for this folder"
STORAGE_ERROR_TITLE: Final[str] = "InfinitePrep Cannot Start"


@dataclass(frozen=True)
class FallbackPrompt:
    """The prompt with the controls that decide its answer."""

    box: QMessageBox
    use_per_user_button: QAbstractButton
    remember_checkbox: QCheckBox

    def answer(self) -> FallbackAnswer:
        """Return the answer from the clicked button and the Remember checkbox."""
        use_per_user = self.box.clickedButton() is self.use_per_user_button
        remember = use_per_user and self.remember_checkbox.isChecked()
        return FallbackAnswer(use_per_user=use_per_user, remember=remember)


def build_fallback_prompt(folder: Path) -> FallbackPrompt:
    """Return the prompt. Its text is plain, never rich text, since it shows a folder path."""
    box = QMessageBox(QMessageBox.Icon.Warning, FALLBACK_TITLE, "")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(FALLBACK_TEXT_TEMPLATE.format(folder=folder))
    use_button = box.addButton(USE_PER_USER_LABEL, QMessageBox.ButtonRole.AcceptRole)
    quit_button = box.addButton(QUIT_LABEL, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(quit_button)
    remember = QCheckBox(REMEMBER_LABEL, box)
    box.setCheckBox(remember)
    return FallbackPrompt(box, use_button, remember)


def build_storage_error_box(message: str) -> QMessageBox:
    """Return the startup storage error box. Its text is plain, since it can hold a path."""
    box = QMessageBox(QMessageBox.Icon.Critical, STORAGE_ERROR_TITLE, "")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(message)
    return box


def show_storage_error(message: str) -> None:
    """Show why storage could not open, and wait until it is closed."""
    build_storage_error_box(message).exec()


def confirm_storage_fallback(folder: Path) -> FallbackAnswer:
    """Ask whether to use per-user storage for this run, and whether to remember it."""
    prompt = build_fallback_prompt(folder)
    prompt.box.exec()
    return prompt.answer()
