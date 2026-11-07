"""Behavioral test: a batch that finishes inside a progress update never crashes the dialog."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.ui.folder_import import FolderImportController

DONE = 1
TOTAL = 2
PATH = "track.wav"


def test_finish_during_set_value_leaves_no_dialog_and_no_timer(qtbot: Any) -> None:
    """A finish that runs inside QProgressDialog.setValue ends the update cleanly.

    On a modal dialog, setValue processes pending events. A queued finished signal can
    then run _finish, which clears the dialog reference. The progress update must not
    touch the cleared reference afterwards.
    """
    parent = QWidget()
    qtbot.addWidget(parent)
    import_service = ImportService(Repository(DatabaseManager(":memory:")))
    controller = FolderImportController(import_service, parent)
    dialog = controller._build_progress_dialog()
    controller._dialog = dialog
    labels: list[str] = []
    original_set_value = dialog.setValue

    def set_value_then_finish(value: int) -> None:
        """Record the label shown so far, then deliver the finish the event loop held."""
        labels.append(dialog.labelText())
        original_set_value(value)
        controller._finish()

    dialog.setValue = set_value_then_finish

    controller._on_progress(DONE, TOTAL, PATH)

    assert labels == [f"Importing {DONE} of {TOTAL}:\n{PATH}"]
    assert controller._dialog is None
    assert not controller._stuck_timer.isActive()
