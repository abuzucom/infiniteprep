"""Behavioral tests: a selection never stops playback (issue 249, D407).

The library table shows one file while the player holds another. The play paths
load the selected file, and a drop loads a track paused.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMenu, QSplitter

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import detail_panel as detail_panel_module
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.audio_player import FILE_ID_MIME_TYPE
from infiniteprep.ui.library_columns import LibraryColumn
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

TRACK_SECONDS = 2.0
SEEK_SECONDS = 0.5
FIRST_NAME = "first.wav"
SECOND_NAME = "second.wav"
WINDOW_WIDTH = 1360
WINDOW_HEIGHT = 820
TABLE_HEIGHT_PX = 700
PANEL_HEIGHT_PX = 100
PANEL_MINIMUM_HEIGHT_PX = 40


@dataclass
class Library:
    """The window under test with the file IDs and paths of both tracks."""

    window: MainWindow
    first_id: str
    second_id: str
    first_path: Path
    second_path: Path


@pytest.fixture
def library(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Library:
    """Open a window over two imported WAV files with a silent player."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    monkeypatch.setattr(detail_panel_module, "SettingsManager", lambda: manager)
    # The waveform analysis runs off the UI thread. These tests cover the
    # transport, so the pool stays idle.
    monkeypatch.setattr(detail_panel_module.WaveformAnalysisPool, "submit", lambda *a, **k: None)
    repository = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repository, MetadataTagAdapter())
    paths = [
        create_wav_fixture(tmp_path / "crate" / name, duration_sec=TRACK_SECONDS)
        for name in (FIRST_NAME, SECOND_NAME)
    ]
    report = importer.import_paths(paths)
    by_name = {Path(asset.current_path).name: asset for asset in report.imported_files}
    window = MainWindow(
        repository=repository,
        import_service=importer,
        review_service=ReviewService(repository),
    )
    qtbot.addWidget(window)
    return Library(
        window=window,
        first_id=by_name[FIRST_NAME].id,
        second_id=by_name[SECOND_NAME].id,
        first_path=Path(by_name[FIRST_NAME].current_path),
        second_path=Path(by_name[SECOND_NAME].current_path),
    )


def row_for(library: Library, name: str) -> int:
    """Return the table row that shows the file with the given name."""
    table = library.window.file_table
    for row in range(table.rowCount()):
        if table.item(row, 0).text() == name:
            return row
    raise AssertionError(f"The library table has no row for {name}.")


def empty_position(library: Library) -> QPoint:
    """Return a viewport point that rests on no row."""
    window = library.window
    window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    window.show()
    # The detail panel claims the space of a window that never grew. A shorter
    # panel leaves empty table space under the last row.
    window.detail_panel.setMinimumHeight(PANEL_MINIMUM_HEIGHT_PX)
    splitter = window.findChild(QSplitter)
    if splitter is not None:
        splitter.setSizes([TABLE_HEIGHT_PX, PANEL_HEIGHT_PX])
    QApplication.processEvents()
    table = window.file_table
    viewport = table.viewport()
    for x_coord in (5, max(0, viewport.width() - 5)):
        for y_coord in range(viewport.height() - 1, 0, -20):
            point = QPoint(x_coord, y_coord)
            if not table.indexAt(point).isValid():
                return point
    raise AssertionError("The library table covers its whole viewport.")


def select_row(library: Library, name: str) -> None:
    """Select the table row that shows the file with the given name."""
    library.window.file_table.selectRow(row_for(library, name))


def drop_event(file_id: str) -> tuple[QDropEvent, QMimeData]:
    """Build a drop event that carries the file ID of a library row.

    The caller keeps the payload alive. A dropped QMimeData that loses its last
    Python owner leaves the event pointing at freed memory.
    """
    mime_data = QMimeData()
    mime_data.setData(FILE_ID_MIME_TYPE, file_id.encode("utf-8"))
    event = QDropEvent(
        QPointF(10.0, 10.0),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    return event, mime_data


def test_row_click_keeps_playing_track(library: Library) -> None:
    """A selection shows another file and leaves the playing track untouched."""
    panel = library.window.detail_panel
    panel.load_and_play_file_asset(library.first_id)
    panel.audio_player.seek_to_seconds(SEEK_SECONDS)

    select_row(library, SECOND_NAME)

    assert panel.current_file_id == library.second_id
    assert panel.audio_player.current_file_path == library.first_path
    assert panel.audio_player.current_position_sec == pytest.approx(SEEK_SECONDS)
    assert panel.audio_player.play_btn.text() == "Pause"


def test_loaded_label_names_loaded_file(library: Library) -> None:
    """The label names the track the player holds and not the selected file."""
    panel = library.window.detail_panel
    panel.load_and_play_file_asset(library.first_id)

    select_row(library, SECOND_NAME)

    assert panel.audio_player.loaded_track_label.text() == f"Loaded: {FIRST_NAME}"
    assert SECOND_NAME not in panel.audio_player.loaded_track_label.text()


def test_the_player_starts_empty(library: Library) -> None:
    """Before the first explicit load the player holds nothing."""
    panel = library.window.detail_panel

    assert panel.loaded_file_id is None
    assert panel.audio_player.loaded_track_label.text() == "No track loaded"
    assert panel.play_selected_btn.isEnabled() is False


def test_play_selected_plays_selection(library: Library) -> None:
    """The button loads the selected file and starts it."""
    panel = library.window.detail_panel
    select_row(library, SECOND_NAME)

    panel.play_selected_btn.click()

    assert panel.audio_player.current_file_path == library.second_path
    assert panel.audio_player.current_position_sec == pytest.approx(0.0)
    assert panel.audio_player.play_btn.text() == "Pause"


def test_cleared_selection_empties_detail_panel(library: Library) -> None:
    """Clearing the table selection leaves no file in the panel and no play button."""
    panel = library.window.detail_panel
    select_row(library, FIRST_NAME)
    assert panel.current_file_id == library.first_id

    library.window.file_table.clearSelection()

    assert panel.current_file_id is None
    assert panel.play_selected_btn.isEnabled() is False
    assert FIRST_NAME not in panel.header_label.text()


def test_cleared_ctrl_enter_loads_nothing(library: Library, qtbot: Any) -> None:
    """Ctrl+Enter loads no file when the table has no selected row."""
    panel = library.window.detail_panel
    select_row(library, FIRST_NAME)
    library.window.file_table.clearSelection()

    qtbot.keyClick(
        library.window.file_table, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier
    )

    assert panel.audio_player.current_file_path is None


def test_play_selected_resumes_loaded_track(library: Library) -> None:
    """The button plays the selected track again when the player already holds it."""
    panel = library.window.detail_panel
    select_row(library, FIRST_NAME)
    panel.load_and_play_file_asset(library.first_id)
    panel.audio_player.stop_playback()
    panel.audio_player.seek_to_seconds(SEEK_SECONDS)

    panel.play_selected_btn.click()

    assert panel.audio_player.play_btn.text() == "Pause"
    assert panel.audio_player.current_position_sec == pytest.approx(SEEK_SECONDS)


def test_play_button_toggles_loaded_track(library: Library) -> None:
    """The player button pauses the loaded track when the selection matches it."""
    panel = library.window.detail_panel
    select_row(library, FIRST_NAME)
    panel.load_and_play_file_asset(library.first_id)

    panel.audio_player.play_btn.click()

    assert panel.audio_player.play_btn.text() == "Play"
    assert panel.audio_player.current_file_path == library.first_path


def test_play_button_loads_new_selection(library: Library) -> None:
    """The play button loads the selection when it differs from the loaded track."""
    panel = library.window.detail_panel
    panel.load_and_play_file_asset(library.first_id)
    select_row(library, SECOND_NAME)

    panel.audio_player.play_btn.click()

    assert panel.audio_player.current_file_path == library.second_path
    assert panel.audio_player.play_btn.text() == "Pause"


def test_ctrl_enter_loads_plays_selection(library: Library, qtbot: Any) -> None:
    """Ctrl+Enter on the library table loads and plays the selected file."""
    panel = library.window.detail_panel
    select_row(library, SECOND_NAME)

    qtbot.keyClick(
        library.window.file_table, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier
    )

    assert panel.audio_player.current_file_path == library.second_path
    assert panel.audio_player.play_btn.text() == "Pause"


def test_context_menu_play_plays_row(library: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    """The right-click selects the row under the pointer and offers a Play item."""
    panel = library.window.detail_panel
    menus: list[QMenu] = []

    def capture(menu: QMenu, position: QPoint) -> None:
        menus.append(menu)

    monkeypatch.setattr(library.window, "_exec_context_menu", capture)
    row = row_for(library, SECOND_NAME)
    # Artist never hides. Filename hides in a table narrower than 1100 px.
    item = library.window.file_table.item(row, LibraryColumn.ARTIST)
    assert item is not None
    position = library.window.file_table.visualItemRect(item).center()

    library.window.file_table.customContextMenuRequested.emit(position)

    assert library.window.file_table.currentRow() == row
    assert len(menus) == 1
    actions = menus[0].actions()
    assert [action.text() for action in actions] == ["Play"]
    actions[0].trigger()
    assert panel.audio_player.current_file_path == library.second_path
    assert panel.audio_player.play_btn.text() == "Pause"


def test_menu_outside_row_opens_nothing(library: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    """A right-click on empty table space opens no menu and plays nothing."""
    panel = library.window.detail_panel
    menus: list[QMenu] = []

    def capture(menu: QMenu, position: QPoint) -> None:
        menus.append(menu)

    monkeypatch.setattr(library.window, "_exec_context_menu", capture)
    select_row(library, FIRST_NAME)

    library.window.file_table.customContextMenuRequested.emit(empty_position(library))

    assert menus == []
    assert panel.audio_player.current_file_path is None


def test_double_click_loads_plays_row(library: Library) -> None:
    """A double-click on a row loads and plays that file."""
    panel = library.window.detail_panel

    library.window.file_table.cellDoubleClicked.emit(row_for(library, SECOND_NAME), 0)

    assert panel.audio_player.current_file_path == library.second_path
    assert panel.audio_player.play_btn.text() == "Pause"


def test_idle_drop_loads_track_paused(library: Library) -> None:
    """A drop loads the track paused at the start."""
    panel = library.window.detail_panel

    event, _payload = drop_event(library.second_id)
    panel.audio_player.waveform_bar.dropEvent(event)

    assert panel.audio_player.current_file_path == library.second_path
    assert panel.audio_player.current_position_sec == pytest.approx(0.0)
    assert panel.audio_player.play_btn.text() == "Play"


def test_playing_drop_cancel_keeps_track(library: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling the warning keeps the playing track and its position."""
    panel = library.window.detail_panel
    panel.load_and_play_file_asset(library.first_id)
    panel.audio_player.seek_to_seconds(SEEK_SECONDS)
    asked: list[tuple[str, str]] = []

    def decline(playing_name: str, dropped_name: str) -> bool:
        asked.append((playing_name, dropped_name))
        return False

    monkeypatch.setattr(panel, "_confirm_replace_playing_track", decline)

    event, _payload = drop_event(library.second_id)
    panel.audio_player.waveform_bar.dropEvent(event)

    assert asked == [(FIRST_NAME, SECOND_NAME)]
    assert panel.audio_player.current_file_path == library.first_path
    assert panel.audio_player.current_position_sec == pytest.approx(SEEK_SECONDS)
    assert panel.audio_player.play_btn.text() == "Pause"


def test_playing_drop_confirm_loads_paused(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirm loads the dropped track paused and stops the playing one."""
    panel = library.window.detail_panel
    panel.load_and_play_file_asset(library.first_id)
    monkeypatch.setattr(panel, "_confirm_replace_playing_track", lambda playing, dropped: True)

    event, _payload = drop_event(library.second_id)
    panel.audio_player.waveform_bar.dropEvent(event)

    assert panel.audio_player.current_file_path == library.second_path
    assert panel.audio_player.current_position_sec == pytest.approx(0.0)
    assert panel.audio_player.play_btn.text() == "Play"


def test_the_warning_names_both_tracks(library: Library) -> None:
    """The warning names the playing track and the dropped track."""
    panel = library.window.detail_panel

    box = panel._build_replace_track_box(FIRST_NAME, SECOND_NAME)

    # QMessageBox ignores window titles on macOS by design, following Apple's guidelines.
    if sys.platform != "darwin":
        assert box.windowTitle() == "Replace the playing track?"
    assert f"'{FIRST_NAME}' is playing." in box.text()
    assert f"'{SECOND_NAME}' stops it and starts the new track paused." in box.text()
    labels = [button.text() for button in box.buttons()]
    assert "Cancel" in labels
    assert "Load paused" in labels
    assert box.defaultButton().text() == "Cancel"


def test_drag_carries_file_id(library: Library) -> None:
    """The drag payload holds the file ID of the dragged row."""
    select_row(library, SECOND_NAME)

    mime_data = library.window.file_table.drag_mime_data()

    assert mime_data is not None
    assert mime_data.hasFormat(FILE_ID_MIME_TYPE)
    assert bytes(mime_data.data(FILE_ID_MIME_TYPE)).decode("utf-8") == library.second_id


def test_external_drop_is_ignored(library: Library) -> None:
    """A file from the file manager carries no file ID and changes nothing."""
    panel = library.window.detail_panel
    external = QMimeData()
    external.setText("C:/music/other.mp3")
    enter_event = QDragEnterEvent(
        QPointF(5.0, 5.0),
        Qt.DropAction.CopyAction,
        external,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    panel.audio_player.waveform_bar.dragEnterEvent(enter_event)

    assert enter_event.isAccepted() is False
    assert panel.audio_player.waveform_bar.drop_highlight is False


def test_library_drag_marks_waveform_target(library: Library) -> None:
    """A library drag over the waveform shows the drop mark."""
    panel = library.window.detail_panel
    mime_data = QMimeData()
    mime_data.setData(FILE_ID_MIME_TYPE, library.second_id.encode("utf-8"))
    enter_event = QDragEnterEvent(
        QPointF(5.0, 5.0),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    panel.audio_player.waveform_bar.dragEnterEvent(enter_event)

    assert enter_event.isAccepted() is True
    assert panel.audio_player.waveform_bar.drop_highlight is True
