"""Tests for the plain copy of the batch queue and its stuck prompts (#342 item 12)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.ui import batch_queue as batch_queue_module
from infiniteprep.ui import folder_import as folder_import_module
from infiniteprep.ui import stuck_file_watcher as watcher_module
from infiniteprep.ui.batch_queue import (
    ANALYZE_STUCK_PROMPT,
    TRANSCODE_STUCK_PROMPT,
    WRITE_STUCK_PROMPT,
    BatchQueueController,
)
from infiniteprep.ui.batch_queue_dock import BatchQueueDock
from infiniteprep.ui.folder_import import FolderImportController, stuck_import_text

TWO_MINUTES_MS = 120_000


def test_stuck_buttons_and_titles_are_plain() -> None:
    """The stuck prompts use sentence-case buttons and plain titles."""
    assert watcher_module.QUIT_NOW_TEXT == "Quit now"
    assert watcher_module.KEEP_WAITING_TEXT == "Keep waiting"
    assert ANALYZE_STUCK_PROMPT.title == "Analysis isn't responding"
    assert WRITE_STUCK_PROMPT.title == "Saving isn't responding"
    assert TRANSCODE_STUCK_PROMPT.title == "Transcode isn't responding"
    assert folder_import_module.STUCK_TITLE == "Import isn't responding"


def test_stuck_texts_are_plain() -> None:
    """Each stuck prompt names its limit, the two choices, and what stays done."""
    assert WRITE_STUCK_PROMPT.text == (
        "One file has taken more than 2 minutes to save. Quit now closes InfinitePrep "
        "right away. Files already saved stay saved. Next time InfinitePrep starts, it "
        "offers to restore or finish this file. Keep waiting lets the save continue."
    )
    assert ANALYZE_STUCK_PROMPT.text == (
        "One file has taken more than 5 minutes to analyze. Quit now closes InfinitePrep "
        "right away. Files already analyzed keep their results. Keep waiting lets the "
        "analysis continue."
    )
    assert TRANSCODE_STUCK_PROMPT.text == (
        "One file has taken more than 5 minutes to transcode. Quit now closes InfinitePrep "
        "right away. Files already transcoded stay transcoded. Keep waiting lets the "
        "transcode continue."
    )
    assert stuck_import_text(TWO_MINUTES_MS) == (
        "One file has taken more than 2 minutes to import. Quit now closes InfinitePrep "
        "right away and keeps nothing from this import. Keep waiting lets the import continue."
    )


def test_cancel_notes_are_plain() -> None:
    """A pending cancel says the batch stops after the current file."""
    assert batch_queue_module.CANCELLING_TEXT == "Stopping after the current file."
    assert folder_import_module.CANCELLING_TEXT == (
        "Stopping after the current file. Nothing will be imported."
    )


def test_queue_panel_labels_are_plain(qtbot: Any) -> None:
    """The queue panel and its buttons use plain, sentence-case names."""
    dock = BatchQueueDock(_idle_controller(qtbot))
    qtbot.addWidget(dock)

    assert dock.windowTitle() == "Queue"
    assert [
        dock.move_up_btn.text(),
        dock.move_down_btn.text(),
        dock.cancel_btn.text(),
        dock.view_result_btn.text(),
        dock.clear_finished_btn.text(),
    ] == ["Move up", "Move down", "Cancel", "See results", "Clear finished"]


def _idle_controller(qtbot: Any) -> BatchQueueController:
    """Return a queue controller with nothing queued, owned by a widget qtbot closes.

    The controller never starts a worker, so closing its parent ends no running thread.
    """
    parent = QWidget()
    qtbot.addWidget(parent)
    repository = Repository(DatabaseManager(":memory:"))
    import_controller = FolderImportController(
        ImportService(repository), parent, show_progress_dialog=False
    )
    return BatchQueueController(import_controller, repository.db.close, parent)
