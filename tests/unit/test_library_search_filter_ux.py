"""Search, filter, and sort feedback of the library window."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, ReviewStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.library_search import (
    ADD_FOLDER_TEXT,
    DELETE_SEARCH_MENU_TEXT,
    EMPTY_LIBRARY_TEXT,
    NO_MATCHES_TEXT,
    RENAME_SEARCH_MENU_TEXT,
    STALE_RESULTS_TEXT,
    format_result_summary,
)
from infiniteprep.ui.library_sidebar import FILTER_LABELS
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.table_sort import DURATION_COLUMN, FILENAME_COLUMN, FORMAT_COLUMN
from infiniteprep.ui.workflow import LibraryFilter
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

TRACK_DURATION_SECONDS = 0.5


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep the window settings in memory. No test reads the settings file on disk."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def import_tracks(tmp_path: Path, relative_paths: tuple[str, ...]) -> tuple[Repository, dict]:
    """Import one short WAV per relative path and return the file IDs by file name."""
    repository = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repository, MetadataTagAdapter())
    paths = [
        create_wav_fixture(tmp_path / relative, duration_sec=TRACK_DURATION_SECONDS)
        for relative in relative_paths
    ]
    report = importer.import_paths(paths) if paths else None
    imported = report.imported_files if report else []
    return repository, {Path(asset.current_path).name: asset.id for asset in imported}


def open_window(qtbot: Any, repository: Repository) -> MainWindow:
    """Open a library window over the repository."""
    window = MainWindow(
        repository=repository,
        import_service=ImportService(repository, MetadataTagAdapter()),
        review_service=ReviewService(repository),
    )
    qtbot.addWidget(window)
    return window


def visible_names(window: MainWindow) -> list[str]:
    """Return the file names in table order."""
    table = window.file_table
    return [table.item(row, FILENAME_COLUMN).text() for row in range(table.rowCount())]


def build_window(qtbot: Any, tmp_path: Path, *names: str) -> tuple[MainWindow, Repository, dict]:
    """Import the named tracks into one crate folder and open a window over the library."""
    repository, ids = import_tracks(tmp_path, tuple(f"crate/{name}" for name in names))
    return open_window(qtbot, repository), repository, ids


def test_typed_search_waits_for_pause(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """Typed text filters the table after a pause, not on each key."""
    window, _repository, _ids = build_window(qtbot, tmp_path, "a.wav", "b.wav")

    qtbot.keyClicks(window.search_input, "b.wav")

    assert visible_names(window) == ["a.wav", "b.wav"]
    qtbot.waitUntil(lambda: visible_names(window) == ["b.wav"])


def test_typed_error_waits_for_pause(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A half-typed query shows no error until the pause, then marks the old results."""
    window, _repository, _ids = build_window(qtbot, tmp_path, "a.wav")

    qtbot.keyClicks(window.search_input, "bpm >")

    assert window.search_error.text() == ""
    qtbot.waitUntil(lambda: bool(window.search_error.text()))
    assert window.active_filter_bar.summary_label.text() == STALE_RESULTS_TEXT


def test_empty_library_offers_folder(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """An empty library says so and offers to add a folder."""
    repository, _ids = import_tracks(tmp_path, ())
    window = open_window(qtbot, repository)
    banner = window.empty_state

    assert not banner.isHidden()
    assert banner.message_label.text() == EMPTY_LIBRARY_TEXT
    assert banner.add_folder_btn.text() == ADD_FOLDER_TEXT
    assert not banner.add_folder_btn.isHidden()
    assert banner.clear_btn.isHidden()


def test_no_matches_offers_clear(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A search with no matches says so, and its action clears the search and filter."""
    window, _repository, _ids = build_window(qtbot, tmp_path, "a.wav", "b.wav")
    window.sidebar.filter_buttons[LibraryFilter.NEEDS_REVIEW].click()
    window.search_input.setText("zzz")
    banner = window.empty_state

    assert not banner.isHidden()
    assert banner.message_label.text() == NO_MATCHES_TEXT
    assert banner.add_folder_btn.isHidden()
    banner.clear_btn.click()

    assert window.search_input.text() == ""
    assert window.sidebar.current_filter() is LibraryFilter.ALL
    assert visible_names(window) == ["a.wav", "b.wav"]
    assert banner.isHidden()


def test_result_summary_counts_tracks(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """The summary shows the shown and total track counts."""
    window, _repository, _ids = build_window(qtbot, tmp_path, "a.wav", "b.wav")
    summary = window.active_filter_bar.summary_label

    assert summary.text() == "2 tracks"
    window.search_input.setText("b.wav")
    assert summary.text() == "Showing 1 of 2 tracks"
    assert format_result_summary(1, 1) == "1 track"


def test_filter_counts_beside_buttons(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """Each library filter shows how many tracks it holds. The button text stays the label."""
    window, repository, ids = build_window(qtbot, tmp_path, "a.wav", "b.wav")
    repository.update_file_status(ids["b.wav"], review_status=ReviewStatus.NO_MATCH)
    window.refresh_file_table()
    counts = window.sidebar.filter_count_labels

    assert counts[LibraryFilter.ALL].text() == "2"
    assert counts[LibraryFilter.NEEDS_REVIEW].text() == "1"
    assert counts[LibraryFilter.READY_TO_WRITE].text() == "0"
    needs_review = window.sidebar.filter_buttons[LibraryFilter.NEEDS_REVIEW]
    assert needs_review.text() == FILTER_LABELS[LibraryFilter.NEEDS_REVIEW]


def test_filter_chip_removes_filter(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """The active filter shows as a chip above the table. A click on the chip removes it."""
    window, _repository, _ids = build_window(qtbot, tmp_path, "a.wav")
    chip = window.active_filter_bar.status_chip

    assert chip.isHidden()
    window.sidebar.filter_buttons[LibraryFilter.HAS_WARNINGS].click()
    assert not chip.isHidden()
    assert FILTER_LABELS[LibraryFilter.HAS_WARNINGS] in chip.text()

    chip.click()

    assert window.sidebar.current_filter() is LibraryFilter.ALL
    assert chip.isHidden()


def test_clear_all_resets_everything(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """Clear all empties the search and resets the library and folder filters."""
    repository, _ids = import_tracks(tmp_path, ("crate/a.wav", "other/b.wav"))
    window = open_window(qtbot, repository)
    clear_all = window.active_filter_bar.clear_all_btn

    assert clear_all.isHidden()
    window.sidebar.folder_labels[0].click()
    window.sidebar.filter_buttons[LibraryFilter.NEEDS_REVIEW].click()
    window.search_input.setText("a")
    assert not clear_all.isHidden()

    clear_all.click()

    assert window.search_input.text() == ""
    assert window.sidebar.current_filter() is LibraryFilter.ALL
    assert window.sidebar.current_folder() == ""
    assert visible_names(window) == ["a.wav", "b.wav"]
    assert clear_all.isHidden()


def test_search_field_clear_keys(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """The field has a clear button, Find selects its text, and Escape empties it."""
    window, _repository, _ids = build_window(qtbot, tmp_path, "a.wav", "b.wav")
    window.search_input.setText("b.wav")

    assert window.search_input.isClearButtonEnabled()
    assert window.find_shortcut.key() == QKeySequence(QKeySequence.StandardKey.Find)
    window.find_shortcut.activated.emit()
    assert window.search_input.selectedText() == "b.wav"

    qtbot.keyClick(window.search_input, Qt.Key.Key_Escape)

    assert window.search_input.text() == ""
    assert visible_names(window) == ["a.wav", "b.wav"]


def test_plain_search_bolds_matches(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A plain search marks the cells that hold the term in bold."""
    window, _repository, _ids = build_window(qtbot, tmp_path, "a.wav", "b.wav")

    window.search_input.setText("b.wav")

    assert window.file_table.item(0, FILENAME_COLUMN).font().bold()
    assert not window.file_table.item(0, FORMAT_COLUMN).font().bold()
    window.search_input.setText("")
    assert not window.file_table.item(0, FILENAME_COLUMN).font().bold()


def test_hidden_field_match_tooltip(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A match on a field without a column names the field and value on the row."""
    window, _repository, ids = build_window(qtbot, tmp_path, "a.wav", "b.wav")
    window.review_service.set_manual_field_value(ids["b.wav"], FieldName.GENRE.value, "House")
    window.refresh_file_table()

    window.search_input.setText("genre:house")

    assert visible_names(window) == ["b.wav"]
    assert window.file_table.item(0, FILENAME_COLUMN).toolTip() == "Matched genre: House"


def test_sort_choice_persists(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """The sort column and direction survive a new window."""
    window, repository, _ids = build_window(qtbot, tmp_path, "a.wav")
    header = window.file_table.horizontalHeader()

    header.sectionClicked.emit(DURATION_COLUMN)
    header.sectionClicked.emit(DURATION_COLUMN)

    assert settings.settings.library_sort_column == DURATION_COLUMN
    assert settings.settings.library_sort_descending
    reopened = open_window(qtbot, repository)
    assert reopened.sort_column_combo.currentIndex() == DURATION_COLUMN
    assert reopened.sort_direction_btn.text() == main_window_module.SORT_DESCENDING_TEXT
    assert reopened.file_table.horizontalHeader().sortIndicatorOrder() == (
        Qt.SortOrder.DescendingOrder
    )


def test_invalid_stored_sort_ignored(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A stored sort column outside the table falls back to the file name."""
    settings.settings.library_sort_column = 99
    window, _repository, _ids = build_window(qtbot, tmp_path, "a.wav")

    assert window.sort_column_combo.currentIndex() == FILENAME_COLUMN


def test_saved_search_resets_filters(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A saved search shows its whole result, as its smart playlist does."""
    window, repository, _ids = build_window(qtbot, tmp_path, "a.wav", "b.wav")
    repository.collections.save_search("Track B", "b.wav")
    window.sidebar.filter_buttons[LibraryFilter.HAS_WARNINGS].click()
    window._refresh_search_menu()

    action = next(item for item in window.search_menu.actions() if item.text() == "Track B")
    action.trigger()

    assert window.sidebar.current_filter() is LibraryFilter.ALL
    assert visible_names(window) == ["b.wav"]


def find_submenu_action(window: MainWindow, menu_text: str, item_text: str) -> Any:
    """Return the action named item_text in the saved search submenu named menu_text."""
    window._refresh_search_menu()
    submenu = next(item.menu() for item in window.search_menu.actions() if item.text() == menu_text)
    return next(item for item in submenu.actions() if item.text() == item_text)


def test_saved_search_menu_deletes(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delete submenu removes a saved search after a confirmation."""
    window, repository, _ids = build_window(qtbot, tmp_path, "a.wav")
    repository.collections.save_search("Track B", "b.wav")
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        staticmethod(lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes),
    )

    find_submenu_action(window, DELETE_SEARCH_MENU_TEXT, "Track B").trigger()

    assert repository.collections.saved_searches() == []


def test_saved_search_menu_renames(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rename submenu gives a saved search a new name."""
    window, repository, _ids = build_window(qtbot, tmp_path, "a.wav")
    repository.collections.save_search("Track B", "b.wav")
    monkeypatch.setattr(
        main_window_module.QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: ("Second", True)),
    )

    find_submenu_action(window, RENAME_SEARCH_MENU_TEXT, "Track B").trigger()

    assert repository.collections.saved_searches() == [("Second", "b.wav")]


def test_folder_row_filters_tracks(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A folder row shows only its tracks. A second click shows every track again."""
    repository, _ids = import_tracks(tmp_path, ("crate/a.wav", "other/b.wav"))
    window = open_window(qtbot, repository)
    crate = window.sidebar.folder_labels[0]
    chip = window.active_filter_bar.folder_chip

    assert crate.text() == "crate  1"
    crate.click()
    assert visible_names(window) == ["a.wav"]
    assert "crate" in chip.text()
    assert not chip.isHidden()

    crate.click()

    assert visible_names(window) == ["a.wav", "b.wav"]
    assert chip.isHidden()
