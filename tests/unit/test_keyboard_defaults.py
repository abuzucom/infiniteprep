"""Behavioral tests for the keyboard paths through the write preview and the tables."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.detail_panel import COLUMN_ACTIONS, COLUMN_APPROVED_EDIT, DetailPanel
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.write_plan_dialog import WritePlanDialog
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

TRACK_NAMES: tuple[str, ...] = ("a.wav", "b.wav", "c.wav")


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep the window settings in memory so no test reads the user settings file."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def import_tracks(tmp_path: Path) -> tuple[Repository, list[str]]:
    """Import three short WAV files and return the repository with their file IDs."""
    repository = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repository, MetadataTagAdapter())
    paths = [create_wav_fixture(tmp_path / "crate" / name) for name in TRACK_NAMES]
    report = importer.import_paths(paths)
    return repository, [asset.id for asset in report.imported_files]


def show_focused(qtbot: Any, widget: QWidget) -> None:
    """Show a top-level widget and make it the active window, so key events reach it."""
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    widget.activateWindow()
    qtbot.waitUntil(widget.isActiveWindow)


def is_inside(widget: QWidget | None, ancestor: QWidget) -> bool:
    """Return True when widget is ancestor or one of its descendants."""
    return widget is not None and (widget is ancestor or ancestor.isAncestorOf(widget))


def test_enter_in_template_field_keeps_write_preview_open(qtbot: Any) -> None:
    """Enter in the rename template field never applies the writes and closes the dialog."""
    dialog = WritePlanDialog(WriteService(Repository(DatabaseManager(":memory:"))), [])
    show_focused(qtbot, dialog)
    dialog.template_input.setFocus()
    qtbot.waitUntil(dialog.template_input.hasFocus)

    qtbot.keyClick(dialog.template_input, Qt.Key.Key_Return)

    assert dialog.isVisible()
    assert dialog.result() != WritePlanDialog.DialogCode.Accepted


def test_tab_leaves_library_table(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """One Tab press moves focus out of the library table, not to its next cell."""
    repository, _file_ids = import_tracks(tmp_path)
    window = MainWindow(
        repository=repository,
        import_service=ImportService(repository, MetadataTagAdapter()),
        review_service=ReviewService(repository),
    )
    show_focused(qtbot, window)
    window.file_table.setFocus()
    window.file_table.setCurrentCell(0, 0)
    qtbot.waitUntil(window.file_table.hasFocus)

    qtbot.keyClick(window.file_table, Qt.Key.Key_Tab)

    assert not is_inside(QApplication.focusWidget(), window.file_table)


def test_tab_from_field_edit_reaches_row_action_button(qtbot: Any, tmp_path: Path) -> None:
    """Tab from a field's edit box lands on a button in the same row's action cell."""
    repository, file_ids = import_tracks(tmp_path)
    panel = DetailPanel(repository, ReviewService(repository))
    show_focused(qtbot, panel)
    panel.load_file_asset(file_ids[0])
    edit_box = panel.fields_table.cellWidget(0, COLUMN_APPROVED_EDIT)
    action_cell = panel.fields_table.cellWidget(0, COLUMN_ACTIONS)
    edit_box.setFocus()
    qtbot.waitUntil(edit_box.hasFocus)

    qtbot.keyClick(edit_box, Qt.Key.Key_Tab)

    focused = QApplication.focusWidget()
    assert isinstance(focused, QPushButton)
    assert is_inside(focused, action_cell)
