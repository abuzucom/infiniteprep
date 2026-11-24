"""Behavioral tests for the library table: hide rules, header menu, and cells."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, KeyMode, PitchClass, ReviewStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.library_columns import (
    NEVER_HIDDEN,
    RESET_COLUMNS_LABEL,
    STATUS_STATES,
    ColumnVisibility,
    LibraryColumn,
    format_column_choices,
    is_column_hidden,
    key_cell_style,
    parse_column_choices,
    status_icon,
)
from infiniteprep.ui.main_window import LIBRARY_COLUMN_WEIGHTS, TABLE_COLUMNS, MainWindow
from infiniteprep.ui.tokens import (
    DARK_PALETTE,
    STANDARD_TABLE_ROW_HEIGHT,
    StatusRole,
    key_cell_colors,
)
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

WIDE_TABLE = 1200
MEDIUM_TABLE = 1050
NARROW_TABLE = 900
TABLE_HEIGHT = 400


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep the window settings in memory so no test reads the user settings file."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def build_window(qtbot: Any, tmp_path: Path) -> tuple[MainWindow, Repository]:
    """Import one short WAV and open a window over it."""
    repository = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repository, MetadataTagAdapter())
    importer.import_paths([create_wav_fixture(tmp_path / "crate" / "a.wav", duration_sec=0.5)])
    window = MainWindow(
        repository=repository, import_service=importer, review_service=ReviewService(repository)
    )
    qtbot.addWidget(window)
    return window, repository


def visibility_table(
    qtbot: Any, width: int, choices: dict[int, bool] | None = None
) -> tuple[QTableWidget, ColumnVisibility]:
    """Return a library-shaped table at a width, with the hide rules applied."""
    table = QTableWidget(0, len(LibraryColumn))
    qtbot.addWidget(table)
    table.resize(width, TABLE_HEIGHT)
    visibility = ColumnVisibility(table, TABLE_COLUMNS, choices or {})
    visibility.apply()
    return table, visibility


def hidden_columns(table: QTableWidget) -> set[LibraryColumn]:
    """Return the library columns the header hides."""
    header = table.horizontalHeader()
    return {column for column in LibraryColumn if header.isSectionHidden(column)}


def test_stretch_columns() -> None:
    """Title, Artist, and Filename stretch equally. Every other column fits its cells."""
    assert LIBRARY_COLUMN_WEIGHTS == {
        LibraryColumn.FILENAME: 1,
        LibraryColumn.ARTIST: 1,
        LibraryColumn.TITLE: 1,
    }


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (WIDE_TABLE, set()),
        (MEDIUM_TABLE, {LibraryColumn.LABEL, LibraryColumn.FILENAME}),
        (
            NARROW_TABLE,
            {
                LibraryColumn.LABEL,
                LibraryColumn.FILENAME,
                LibraryColumn.FORMAT,
                LibraryColumn.DURATION,
                LibraryColumn.MIX,
            },
        ),
    ],
)
def test_hide_by_width(qtbot: Any, width: int, expected: set[LibraryColumn]) -> None:
    """Label and Filename need 1100 px. Format, Duration, and Mix leave below 1000 px."""
    table, _visibility = visibility_table(qtbot, width)

    assert hidden_columns(table) == expected


def test_core_columns_stay() -> None:
    """Artist, Title, BPM, Key, Status, and Warnings show even when a choice says hide."""
    choices = {int(column): False for column in LibraryColumn}

    for column in NEVER_HIDDEN:
        assert not is_column_hidden(column, 0, choices)


def test_menu_choice_wins(qtbot: Any) -> None:
    """A column shown from the menu stays at a narrow width. A hidden one stays hidden."""
    table, visibility = visibility_table(qtbot, NARROW_TABLE)

    visibility.set_choice(LibraryColumn.FILENAME, shown=True)
    table.resize(WIDE_TABLE, TABLE_HEIGHT)
    visibility.set_choice(LibraryColumn.MIX, shown=False)

    assert not table.horizontalHeader().isSectionHidden(LibraryColumn.FILENAME)
    assert table.horizontalHeader().isSectionHidden(LibraryColumn.MIX)
    table.resize(NARROW_TABLE, TABLE_HEIGHT)
    assert not table.horizontalHeader().isSectionHidden(LibraryColumn.FILENAME)


def test_reset_columns(qtbot: Any) -> None:
    """Reset columns drops every choice, so the width rules decide again."""
    table, visibility = visibility_table(qtbot, NARROW_TABLE)
    visibility.set_choice(LibraryColumn.FILENAME, shown=True)

    visibility.reset_choices()

    assert visibility.choices == {}
    assert LibraryColumn.FILENAME in hidden_columns(table)


def test_header_menu(qtbot: Any) -> None:
    """The menu checks each shown column, locks the core six, and ends with Reset columns."""
    _table, visibility = visibility_table(qtbot, MEDIUM_TABLE)

    menu = visibility.build_menu()
    actions = [action for action in menu.actions() if not action.isSeparator()]
    by_label = {action.text(): action for action in actions}

    assert [action.text() for action in actions] == [*TABLE_COLUMNS, RESET_COLUMNS_LABEL]
    assert not by_label[TABLE_COLUMNS[LibraryColumn.FILENAME]].isChecked()
    assert by_label[TABLE_COLUMNS[LibraryColumn.FORMAT]].isChecked()
    assert not by_label[TABLE_COLUMNS[LibraryColumn.ARTIST]].isEnabled()

    by_label[TABLE_COLUMNS[LibraryColumn.FILENAME]].trigger()

    assert visibility.choices == {int(LibraryColumn.FILENAME): True}


def test_choices_round_trip() -> None:
    """Stored choices load back. Unknown columns, non-booleans, and bad JSON drop."""
    choices = {int(LibraryColumn.MIX): False, int(LibraryColumn.FILENAME): True}

    assert parse_column_choices(format_column_choices(choices)) == choices
    assert parse_column_choices('{"5": false, "99": true, "1": "yes"}') == {5: False}
    assert parse_column_choices("not json") == {}
    assert parse_column_choices("[1, 2]") == {}
    assert format_column_choices({}) == ""


def test_status_icons() -> None:
    """Each review status has an icon in the color of its state."""
    assert set(STATUS_STATES) == set(ReviewStatus)
    assert status_icon("written", DARK_PALETTE) == (
        "circle-check",
        DARK_PALETTE.status[StatusRole.DONE],
    )
    assert status_icon("no_match", DARK_PALETTE) == (
        "triangle-exclamation-point",
        DARK_PALETTE.status[StatusRole.WARNING],
    )
    assert status_icon("error", DARK_PALETTE) == (
        "circle-x",
        DARK_PALETTE.status[StatusRole.FAILED],
    )
    assert status_icon("not_a_status", DARK_PALETTE) is None


def test_key_cell_style() -> None:
    """A minor key takes the minor shade of its wheel number. An unknown key takes none."""
    assert key_cell_style((PitchClass.A, KeyMode.MINOR)) == key_cell_colors(1, is_minor=True)
    assert key_cell_style((PitchClass.C, KeyMode.MAJOR)) == key_cell_colors(1, is_minor=False)
    assert key_cell_style((PitchClass.E, KeyMode.MAJOR)) == key_cell_colors(5, is_minor=False)
    assert key_cell_style(None) is None


def test_status_cell(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """The status cell holds an icon. Its label stays as tooltip and spoken text."""
    window, _repository = build_window(qtbot, tmp_path)

    item = window.file_table.item(0, LibraryColumn.STATUS)

    assert item.text() == ""
    assert not item.icon().isNull()
    assert item.toolTip() == "Not analyzed"
    assert item.data(Qt.ItemDataRole.AccessibleTextRole) == "Not analyzed"


def test_key_cell_color(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """An approved key fills its cell with the key color."""
    window, repository = build_window(qtbot, tmp_path)
    file_id = repository.list_files()[0].id
    window.review_service.set_manual_field_value(file_id, FieldName.KEY.value, "Am")
    window.refresh_file_table()

    item = window.file_table.item(0, LibraryColumn.KEY)
    expected = key_cell_colors(1, is_minor=True)

    assert item.background().color().name() == expected.fill
    assert item.foreground().color().name() == expected.text


def test_row_height(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """Each library row is 24 px tall at the Standard text size."""
    window, _repository = build_window(qtbot, tmp_path)

    assert window.file_table.verticalHeader().defaultSectionSize() == STANDARD_TABLE_ROW_HEIGHT


def test_column_state_persists(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """A menu choice reaches the settings, and a moved column starts the layout save."""
    window, _repository = build_window(qtbot, tmp_path)

    window.column_visibility.set_choice(LibraryColumn.MIX, shown=False)
    window.file_table.horizontalHeader().moveSection(LibraryColumn.BPM, LibraryColumn.FORMAT)

    stored = parse_column_choices(settings.settings.file_table_column_choices)
    assert stored == {int(LibraryColumn.MIX): False}
    assert window._layout_save_timer.isActive()
    assert window.file_table.horizontalHeader().sectionsMovable()
