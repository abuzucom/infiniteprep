"""Behavioral tests for accessible names, read-only tables, and keyboard sorting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QWidget

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.journal_dialog import JournalDialog
from infiniteprep.ui.main_window import (
    SORT_ASCENDING_TEXT,
    SORT_DESCENDING_TEXT,
    TABLE_COLUMNS,
    MainWindow,
)
from infiniteprep.ui.recovery_dialog import RecoveryDialog
from infiniteprep.ui.write_plan_dialog import WritePlanDialog
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

TRACK_NAMES: tuple[str, ...] = ("b.wav", "a.wav", "c.wav")
DURATION_COLUMN: int = TABLE_COLUMNS.index("Duration")


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep the window settings in memory so no test reads the user settings file."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def build_window(qtbot: Any, tmp_path: Path, names: tuple[str, ...]) -> MainWindow:
    """Import one short WAV per name and open a window over them."""
    repository = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repository, MetadataTagAdapter())
    paths = [
        create_wav_fixture(tmp_path / "crate" / name, duration_sec=float(index + 1))
        for index, name in enumerate(names)
    ]
    if paths:
        importer.import_paths(paths)
    window = MainWindow(
        repository=repository, import_service=importer, review_service=ReviewService(repository)
    )
    qtbot.addWidget(window)
    return window


def unnamed_focus_targets(root: QWidget) -> list[str]:
    """Return a description of every Tab-reachable widget with no accessible name."""
    missing: list[str] = []
    for widget in root.findChildren(QWidget):
        if not widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
            continue
        interface = QAccessible.queryAccessibleInterface(widget)
        name = interface.text(QAccessible.Text.Name) if interface is not None else ""
        if not name.strip():
            missing.append(f"{type(widget).__name__} {widget.objectName()!r}")
    return missing


def visible_names(window: MainWindow) -> list[str]:
    """Return the file name shown in each library row."""
    return [window.file_table.item(row, 0).text() for row in range(window.file_table.rowCount())]


def test_every_main_window_focus_target_has_a_name(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A screen reader names every control that Tab reaches in the main window."""
    window = build_window(qtbot, tmp_path, ())

    assert unnamed_focus_targets(window) == []


def test_every_dialog_focus_target_has_a_name(qtbot: Any) -> None:
    """A screen reader names every control that Tab reaches in the write and journal dialogs."""
    repository = Repository(DatabaseManager(":memory:"))
    write_service = WriteService(repository)
    dialogs: list[QWidget] = [
        WritePlanDialog(write_service, []),
        JournalDialog(repository, write_service),
        RecoveryDialog(write_service),
    ]
    for dialog in dialogs:
        qtbot.addWidget(dialog)

    assert [unnamed_focus_targets(dialog) for dialog in dialogs] == [[], [], []]


def test_double_click_never_opens_a_library_cell_editor(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A double-click selects a library row and opens no editor for its computed value."""
    window = build_window(qtbot, tmp_path, ("a.wav",))
    window.show()
    qtbot.waitExposed(window)
    table = window.file_table
    cell_center = table.visualItemRect(table.item(0, 0)).center()

    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=cell_center)

    assert table.state() != QAbstractItemView.State.EditingState
    assert table.item(0, 0).text() == "a.wav"


def test_dialog_tables_are_read_only_and_leave_on_tab(qtbot: Any) -> None:
    """The write, journal, and recovery tables open no editor and pass Tab to the next control."""
    repository = Repository(DatabaseManager(":memory:"))
    write_service = WriteService(repository)
    # The list keeps every dialog alive. A collected dialog deletes its table.
    dialogs: list[QWidget] = [
        WritePlanDialog(write_service, []),
        JournalDialog(repository, write_service),
        RecoveryDialog(write_service),
    ]
    for dialog in dialogs:
        qtbot.addWidget(dialog)
    tables: list[QTableWidget] = [dialog.table for dialog in dialogs]

    no_edit = QAbstractItemView.EditTrigger.NoEditTriggers
    assert [table.editTriggers() == no_edit for table in tables] == [True, True, True]
    assert [table.tabKeyNavigation() for table in tables] == [False, False, False]


def test_space_on_the_direction_button_reverses_the_sort(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """Space on the sort direction button reverses the library order with no mouse."""
    window = build_window(qtbot, tmp_path, TRACK_NAMES)
    window.show()
    qtbot.waitExposed(window)
    assert visible_names(window) == ["a.wav", "b.wav", "c.wav"]
    window.sort_direction_btn.setFocus()

    qtbot.keyClick(window.sort_direction_btn, Qt.Key.Key_Space)

    assert visible_names(window) == ["c.wav", "b.wav", "a.wav"]
    assert window.sort_direction_btn.text() == SORT_DESCENDING_TEXT


def test_sort_picker_sorts_by_the_chosen_column(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """Choosing Duration in the sort picker sorts ascending by length and marks the header."""
    window = build_window(qtbot, tmp_path, TRACK_NAMES)

    window.sort_column_combo.setCurrentIndex(DURATION_COLUMN)

    assert visible_names(window) == list(TRACK_NAMES)
    header = window.file_table.horizontalHeader()
    assert header.sortIndicatorSection() == DURATION_COLUMN
    assert window.sort_direction_btn.text() == SORT_ASCENDING_TEXT


def test_header_click_moves_the_sort_picker(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A header click and the sort controls show the same column and direction."""
    window = build_window(qtbot, tmp_path, TRACK_NAMES)

    window.file_table.horizontalHeader().sectionClicked.emit(DURATION_COLUMN)
    window.file_table.horizontalHeader().sectionClicked.emit(DURATION_COLUMN)

    assert window.sort_column_combo.currentIndex() == DURATION_COLUMN
    assert window.sort_direction_btn.text() == SORT_DESCENDING_TEXT
    assert visible_names(window) == list(reversed(TRACK_NAMES))
