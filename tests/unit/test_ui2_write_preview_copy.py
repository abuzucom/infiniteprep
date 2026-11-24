"""Tests for the plain copy of the write preview (#342 item 12)."""

from __future__ import annotations

from typing import Any

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui import write_plan_dialog as dialog_module
from infiniteprep.ui.write_plan_dialog import WritePlanDialog, format_saved_count


def test_preview_texts_are_plain() -> None:
    """The preview title, controls, and messages use plain words."""
    module = dialog_module
    assert module.WINDOW_TITLE == "Review changes"
    assert module.RENAME_LABEL == "Rename files:"
    assert module.REFRESH_LABEL == "Refresh"
    assert module.PLAN_TREE_NAME == "Changes"
    assert module.SAVE_BLOCKED_TITLE == "Can't save yet"
    assert module.NO_SAFE_PLANS_TEXT == (
        "Nothing to save. Fix the skipped files or close this window."
    )


def test_status_line_counts_saved_files() -> None:
    """The status line says how many of the previewed files get saved."""
    assert format_saved_count(3, 5) == "3 of 5 files will be saved."
    assert format_saved_count(0, 1) == "0 of 1 file will be saved."


def test_dialog_opens_with_plain_labels(qtbot: Any) -> None:
    """An empty preview shows the plain title, buttons, and status line."""
    dialog = WritePlanDialog(WriteService(Repository(DatabaseManager(":memory:"))), [])
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Review changes"
    assert dialog.rename_checkbox.text() == "Rename files:"
    assert dialog.refresh_btn.text() == "Refresh"
    assert dialog.apply_btn.text() == "Save changes"
    assert dialog.plan_tree.accessibleName() == "Changes"
    assert dialog.status_label.text() == "0 of 0 files will be saved."
