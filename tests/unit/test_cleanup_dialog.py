"""Behavioral tests for the Settings cleanup dialog (D83a3b, D294, D296)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from infiniteprep.data_cleanup import CleanupChoice, CleanupReport
from infiniteprep.ui.cleanup_dialog import (
    CONFIRM_KEYS_ITEM,
    CleanupDialog,
    build_cleanup_blocked_box,
    build_cleanup_confirm,
    build_cleanup_result_box,
)

FOLDERS = [Path("/data/share/InfinitePrep"), Path("/data/cache/InfinitePrep")]


class RecordingConfirm:
    """Answer the final prompt with a fixed reply and keep the prompt it was shown."""

    def __init__(self, reply: bool) -> None:
        self.reply = reply
        self.shown: list[QMessageBox] = []

    def __call__(self, box: QMessageBox) -> bool:
        self.shown.append(box)
        return self.reply


def _dialog(qtbot: QtBot, confirm: RecordingConfirm) -> CleanupDialog:
    """Return a cleanup dialog for the sample folders."""
    dialog = CleanupDialog(FOLDERS, confirm=confirm)
    qtbot.addWidget(dialog)
    return dialog


def test_boxes_start_unchecked_and_delete_is_disabled(qtbot: QtBot) -> None:
    """Both boxes start unchecked, so Delete cannot run until one is chosen (D294)."""
    dialog = _dialog(qtbot, RecordingConfirm(reply=True))

    assert not dialog.data_box.isChecked()
    assert not dialog.keys_box.isChecked()
    assert not dialog.delete_btn.isEnabled()
    assert dialog.cancel_btn.isDefault()


def test_checking_a_box_enables_delete(qtbot: QtBot) -> None:
    """Either box enables Delete, and unchecking it disables Delete again."""
    dialog = _dialog(qtbot, RecordingConfirm(reply=True))

    dialog.keys_box.setChecked(True)
    assert dialog.delete_btn.isEnabled()
    dialog.keys_box.setChecked(False)
    assert not dialog.delete_btn.isEnabled()


def test_folder_list_names_every_folder_as_plain_text(qtbot: QtBot) -> None:
    """The dialog shows each folder the data box deletes, as plain text."""
    dialog = _dialog(qtbot, RecordingConfirm(reply=True))

    assert dialog.folder_label.textFormat() == Qt.TextFormat.PlainText
    for folder in FOLDERS:
        assert str(folder) in dialog.folder_label.text()


def test_yes_on_the_final_prompt_accepts_with_the_choice(qtbot: QtBot) -> None:
    """Delete shows the final prompt, and Yes closes the dialog with the chosen boxes."""
    confirm = RecordingConfirm(reply=True)
    dialog = _dialog(qtbot, confirm)
    dialog.data_box.setChecked(True)

    qtbot.mouseClick(dialog.delete_btn, Qt.MouseButton.LeftButton)

    assert len(confirm.shown) == 1
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.choice() == CleanupChoice(delete_data=True, delete_keys=False)


def test_no_on_the_final_prompt_keeps_the_dialog_open(qtbot: QtBot) -> None:
    """No on the final prompt deletes nothing and leaves the dialog unaccepted."""
    dialog = _dialog(qtbot, RecordingConfirm(reply=False))
    dialog.keys_box.setChecked(True)

    qtbot.mouseClick(dialog.delete_btn, Qt.MouseButton.LeftButton)

    assert dialog.result() != QDialog.DialogCode.Accepted


@pytest.mark.usefixtures("qapp")
def test_final_prompt_defaults_to_no_and_lists_the_items() -> None:
    """The final prompt names every folder and the keys, with No as its default (D296)."""
    box = build_cleanup_confirm(CleanupChoice(delete_data=True, delete_keys=True), FOLDERS)

    assert box.defaultButton() is box.button(QMessageBox.StandardButton.No)
    assert box.textFormat() == Qt.TextFormat.PlainText
    for folder in FOLDERS:
        assert str(folder) in box.text()
    assert CONFIRM_KEYS_ITEM in box.text()


@pytest.mark.usefixtures("qapp")
def test_final_prompt_for_keys_only_lists_no_folders() -> None:
    """With only the keys box checked, the prompt names no folder."""
    box = build_cleanup_confirm(CleanupChoice(delete_data=False, delete_keys=True), FOLDERS)

    assert all(str(folder) not in box.text() for folder in FOLDERS)
    assert CONFIRM_KEYS_ITEM in box.text()


@pytest.mark.usefixtures("qapp")
def test_result_box_lists_failures_as_plain_text() -> None:
    """The closing message lists what was removed and what remains (D295)."""
    report = CleanupReport(removed=[FOLDERS[0]], failed=[(FOLDERS[1], "Permission denied")])

    box = build_cleanup_result_box(report)

    assert box.textFormat() == Qt.TextFormat.PlainText
    assert str(FOLDERS[0]) in box.text()
    assert "Permission denied" in box.text()
    assert box.icon() == QMessageBox.Icon.Warning


@pytest.mark.usefixtures("qapp")
def test_blocked_box_says_nothing_was_deleted() -> None:
    """When the window stays open, the message says nothing was deleted."""
    box = build_cleanup_blocked_box()

    assert "nothing was deleted" in box.text()
