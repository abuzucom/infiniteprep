"""Behavioral tests for the main window shell: stages, sidebar, and breakpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QKeySequence

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import ReviewStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.library_sidebar import FILTER_LABELS, NO_FOLDERS_TEXT
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.workflow import (
    DETAIL_PANEL_WIDTH,
    MINIMUM_WINDOW_HEIGHT,
    MINIMUM_WINDOW_WIDTH,
    STAGE_STATUSES,
    LayoutMode,
    LibraryFilter,
    WorkflowStage,
    count_stages,
    find_current_stage,
    find_layout_mode,
    fit_window_on_screens,
    matches_library_filter,
)
from infiniteprep.ui.workflow_bar import (
    STAGE_ACTION_LABELS,
    STAGE_LABELS,
    format_stage_name,
    format_stage_text,
)
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

WIDE_WIDTH = 1700
MEDIUM_WIDTH = 1400
NARROW_WIDTH = 1000
WINDOW_HEIGHT = 800
SCREEN = QRect(0, 0, 1920, 1080)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep the window settings in memory so no test reads the user settings file."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def build_window(
    qtbot: Any, tmp_path: Path, names: tuple[str, ...] = (), **callbacks: Any
) -> tuple[MainWindow, Repository]:
    """Import one short WAV per name and open a window over them."""
    repository = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repository, MetadataTagAdapter())
    paths = [create_wav_fixture(tmp_path / "crate" / name, duration_sec=0.5) for name in names]
    if paths:
        importer.import_paths(paths)
    window = MainWindow(
        repository=repository,
        import_service=importer,
        review_service=ReviewService(repository),
        **callbacks,
    )
    qtbot.addWidget(window)
    return window, repository


def show_at_width(qtbot: Any, window: MainWindow, width: int) -> None:
    """Show the window and resize it, so the layout follows the new width."""
    window.show()
    qtbot.waitExposed(window)
    window.resize(width, WINDOW_HEIGHT)
    qtbot.waitUntil(lambda: window.width() == width)


def visible_names(window: MainWindow) -> list[str]:
    """Return the file name shown in each library row."""
    return [window.file_table.item(row, 0).text() for row in range(window.file_table.rowCount())]


def test_status_stage_map() -> None:
    """Each status belongs to exactly one of Analyze, Review, Apply, and Export."""
    for status in ReviewStatus:
        owners = [stage for stage, statuses in STAGE_STATUSES.items() if status in statuses]
        assert len(owners) == 1, status


def test_stage_counts() -> None:
    """Import counts every track, and each other stage counts its statuses."""
    statuses = ["unprocessed", "error", "no_match", "approved", "written", "written"]

    counts = count_stages(statuses)

    assert counts == {
        WorkflowStage.IMPORT: 6,
        WorkflowStage.ANALYZE: 2,
        WorkflowStage.REVIEW: 1,
        WorkflowStage.APPLY: 1,
        WorkflowStage.EXPORT: 2,
    }


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], WorkflowStage.IMPORT),
        (["unprocessed", "written"], WorkflowStage.ANALYZE),
        (["ready_for_review", "approved"], WorkflowStage.REVIEW),
        (["ready_to_write", "written"], WorkflowStage.APPLY),
        (["written", "applied"], WorkflowStage.EXPORT),
    ],
)
def test_current_stage(statuses: list[str], expected: WorkflowStage) -> None:
    """The current stage is the first one that still has tracks waiting."""
    assert find_current_stage(count_stages(statuses)) is expected


@pytest.mark.parametrize(
    ("chosen", "status", "has_warnings", "expected"),
    [
        (LibraryFilter.ALL, "written", False, True),
        (LibraryFilter.NEEDS_REVIEW, "no_match", False, True),
        (LibraryFilter.NEEDS_REVIEW, "approved", False, False),
        (LibraryFilter.READY_TO_WRITE, "ready_to_write", False, True),
        (LibraryFilter.READY_TO_WRITE, "unprocessed", False, False),
        (LibraryFilter.HAS_WARNINGS, "written", True, True),
        (LibraryFilter.HAS_WARNINGS, "written", False, False),
    ],
)
def test_library_filters(
    chosen: LibraryFilter, status: str, has_warnings: bool, expected: bool
) -> None:
    """Each sidebar filter keeps the tracks it names and drops the rest."""
    assert matches_library_filter(chosen, status, has_warnings) is expected


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (1920, LayoutMode.WIDE),
        (1600, LayoutMode.WIDE),
        (1599, LayoutMode.MEDIUM),
        (1280, LayoutMode.MEDIUM),
        (1279, LayoutMode.NARROW),
        (960, LayoutMode.NARROW),
    ],
)
def test_breakpoints(width: int, expected: LayoutMode) -> None:
    """The layout switches at 1600 and 1280 px."""
    assert find_layout_mode(width) is expected


def test_on_screen_window_stays() -> None:
    """A saved position that a screen still shows stays as saved."""
    saved = QRect(100, 100, 1200, 800)

    assert fit_window_on_screens(saved, [SCREEN]) == saved


def test_offscreen_window_moves() -> None:
    """A position on a monitor that is gone moves to the center of a screen and fits it."""
    saved = QRect(3000, 200, 2400, 900)

    moved = fit_window_on_screens(saved, [SCREEN])

    assert SCREEN.contains(moved)
    assert moved.center() == SCREEN.center()
    assert moved.width() == SCREEN.width()
    assert moved.height() == saved.height()


def test_corner_window_moves() -> None:
    """A window that overlaps a screen only at one corner moves to the screen center."""
    saved = QRect(SCREEN.right(), SCREEN.bottom(), 1200, 800)

    moved = fit_window_on_screens(saved, [SCREEN])

    assert moved.center() == SCREEN.center()
    assert SCREEN.contains(moved)


def test_top_strip_window_stays() -> None:
    """A window keeps its position while a screen shows 120 px of its 32 px top strip."""
    saved = QRect(SCREEN.right() - 200, 100, 1200, 800)

    assert fit_window_on_screens(saved, [SCREEN]) == saved


def test_compact_stage_text() -> None:
    """A compact stage button shows its number and count, and its name stays spoken."""
    assert format_stage_text(WorkflowStage.REVIEW, 4, show_label=True) == "3  Review  4"
    assert format_stage_text(WorkflowStage.REVIEW, 4, show_label=False) == "3  4"
    assert format_stage_name(WorkflowStage.REVIEW, 1) == "Review, 1 track"


def test_minimum_size(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """The window never shrinks below 960 x 600."""
    window, _repository = build_window(qtbot, tmp_path)

    assert window.minimumWidth() == MINIMUM_WINDOW_WIDTH
    assert window.minimumHeight() == MINIMUM_WINDOW_HEIGHT


def test_bar_counts(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """Imported tracks wait on Analyze, so the primary button carries Analyze."""
    window, _repository = build_window(qtbot, tmp_path, ("a.wav", "b.wav"))
    bar = window.workflow_bar

    assert bar.current_stage is WorkflowStage.ANALYZE
    assert bar.primary_button.text() == STAGE_ACTION_LABELS[WorkflowStage.ANALYZE]
    assert bar.stage_buttons[WorkflowStage.IMPORT].accessibleName() == "Import, 2 tracks"
    assert bar.stage_buttons[WorkflowStage.ANALYZE].accessibleName() == "Analyze, 2 tracks"


def test_empty_library(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """With no tracks, Import is current and the sidebar says no folder exists."""
    window, _repository = build_window(qtbot, tmp_path)

    assert window.workflow_bar.current_stage is WorkflowStage.IMPORT
    assert [label.text() for label in window.sidebar.folder_labels] == [NO_FOLDERS_TEXT]


def test_stage_button_action(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """The Analyze stage button and the primary button both analyze the visible tracks."""
    analyzed: list[list[str]] = []
    window, _repository = build_window(
        qtbot, tmp_path, ("a.wav",), on_analyze_batch=analyzed.append
    )

    window.workflow_bar.stage_buttons[WorkflowStage.ANALYZE].click()
    window.workflow_bar.primary_button.click()
    window.sidebar.stage_buttons[WorkflowStage.ANALYZE].click()

    assert analyzed == [window.visible_file_ids] * 3


def test_locked_stage_shortcut(
    qtbot: Any,
    tmp_path: Path,
    settings: MemorySettingsManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While a batch locks Review, Alt+3 approves nothing, like the disabled buttons."""
    messages: list[str] = []
    monkeypatch.setattr(
        main_window_module, "show_plain_message", lambda *args: messages.append(args[2])
    )
    window, _repository = build_window(qtbot, tmp_path, ("a.wav",))
    window.workflow_bar.set_stages_locked({WorkflowStage.REVIEW})

    window.stage_shortcuts[WorkflowStage.REVIEW].activated.emit()

    assert messages == []
    window.workflow_bar.set_stages_locked(set())
    window.stage_shortcuts[WorkflowStage.REVIEW].activated.emit()
    assert len(messages) == 1


def test_stage_shortcuts(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """Alt+2 runs Analyze, and the Analyze button tooltip names that key."""
    analyzed: list[list[str]] = []
    window, _repository = build_window(
        qtbot, tmp_path, ("a.wav",), on_analyze_batch=analyzed.append
    )
    analyze_key = QKeySequence("Alt+2")

    window.stage_shortcuts[WorkflowStage.ANALYZE].activated.emit()

    assert window.stage_shortcuts[WorkflowStage.ANALYZE].key() == analyze_key
    assert analyzed == [window.visible_file_ids]
    shown_key = analyze_key.toString(QKeySequence.SequenceFormat.NativeText)
    assert shown_key in window.workflow_bar.stage_buttons[WorkflowStage.ANALYZE].toolTip()
    assert [window.stage_shortcuts[stage].key() for stage in WorkflowStage] == [
        QKeySequence(f"Alt+{number}") for number in range(1, 6)
    ]


def test_sidebar_folders(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """Each source folder shows its name and its track count."""
    window, _repository = build_window(qtbot, tmp_path, ("a.wav", "b.wav"))

    assert [label.text() for label in window.sidebar.folder_labels] == ["crate  2"]


def test_needs_review_filter(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """Needs review shows only the tracks that wait on Review."""
    window, repository = build_window(qtbot, tmp_path, ("a.wav", "b.wav"))
    file_ids = {Path(asset.current_path).name: asset.id for asset in repository.list_files()}
    repository.update_file_status(file_ids["b.wav"], review_status=ReviewStatus.NO_MATCH)

    window.sidebar.filter_buttons[LibraryFilter.NEEDS_REVIEW].click()

    assert visible_names(window) == ["b.wav"]
    assert (
        window.sidebar.filter_buttons[LibraryFilter.NEEDS_REVIEW].text()
        == (FILTER_LABELS[LibraryFilter.NEEDS_REVIEW])
    )
    window.sidebar.filter_buttons[LibraryFilter.ALL].click()
    assert visible_names(window) == ["a.wav", "b.wav"]


def test_wide_layout(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """From 1600 px the sidebar sits in the window, every label shows, and no toggle shows."""
    window, _repository = build_window(qtbot, tmp_path, ("a.wav",))

    show_at_width(qtbot, window, WIDE_WIDTH)

    assert window.layout_mode is LayoutMode.WIDE
    assert window.body_splitter.indexOf(window.sidebar) == 0
    assert window.sidebar.isVisible()
    assert not window.sidebar_toggle_btn.isVisible()
    assert window.detail_splitter.orientation() == Qt.Orientation.Horizontal
    assert STAGE_LABELS[WorkflowStage.REVIEW] in (
        window.workflow_bar.stage_buttons[WorkflowStage.REVIEW].text()
    )


def test_medium_layout(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """From 1280 to 1599 px the sidebar hides behind a button and labels compact."""
    window, _repository = build_window(qtbot, tmp_path, ("a.wav",))

    show_at_width(qtbot, window, MEDIUM_WIDTH)

    assert window.layout_mode is LayoutMode.MEDIUM
    assert not window.sidebar.isVisible()
    assert window.sidebar_toggle_btn.isVisible()
    assert window.detail_splitter.orientation() == Qt.Orientation.Horizontal
    review_text = window.workflow_bar.stage_buttons[WorkflowStage.REVIEW].text()
    assert STAGE_LABELS[WorkflowStage.REVIEW] not in review_text
    current_text = window.workflow_bar.stage_buttons[WorkflowStage.ANALYZE].text()
    assert STAGE_LABELS[WorkflowStage.ANALYZE] in current_text

    window.sidebar_toggle_btn.click()
    assert window.sidebar.isVisible()
    window.sidebar.filter_buttons[LibraryFilter.HAS_WARNINGS].click()
    assert not window.sidebar.isVisible()


def test_narrow_layout(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """Below 1280 px the detail panel moves under the table."""
    window, _repository = build_window(qtbot, tmp_path, ("a.wav",))

    show_at_width(qtbot, window, NARROW_WIDTH)

    assert window.layout_mode is LayoutMode.NARROW
    assert window.detail_splitter.orientation() == Qt.Orientation.Vertical


def test_panel_width(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """Beside the table, the detail panel starts at 480 px after a layout change."""
    window, _repository = build_window(qtbot, tmp_path, ("a.wav",))
    show_at_width(qtbot, window, NARROW_WIDTH)

    show_at_width(qtbot, window, 1600)

    assert window.layout_mode is LayoutMode.WIDE
    qtbot.waitUntil(lambda: window.detail_splitter.sizes()[1] == DETAIL_PANEL_WIDTH)


def test_geometry_saved(
    qtbot: Any,
    tmp_path: Path,
    settings: MemorySettingsManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed window saves its geometry, and the next window restores those bytes.

    Qt shrinks a restored window to the screen it lands on. The offscreen test screen
    is smaller than the window minimum, so the test checks the bytes Qt receives.
    """
    first, _repository = build_window(qtbot, tmp_path)
    show_at_width(qtbot, first, NARROW_WIDTH)
    saved_state = bytes(first.saveGeometry())
    first.close()
    assert settings.settings.window_geometry
    restored: list[bytes] = []

    def record_restore(_window: MainWindow, state: Any) -> bool:
        restored.append(bytes(state))
        return True

    monkeypatch.setattr(MainWindow, "restoreGeometry", record_restore)
    build_window(qtbot, tmp_path)

    assert restored == [saved_state]


def test_damaged_geometry(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """Invalid saved geometry opens the window at a share of the available screen."""
    settings.settings.window_geometry = "not base64 !"

    window, _repository = build_window(qtbot, tmp_path)

    available = window.screen().availableGeometry()
    assert window.width() == max(MINIMUM_WINDOW_WIDTH, round(available.width() * 0.9))
    assert window.height() == max(MINIMUM_WINDOW_HEIGHT, round(available.height() * 0.9))


def test_key_picker_placement(qtbot: Any, tmp_path: Path, settings: MemorySettingsManager) -> None:
    """The Key Notation label focuses its picker, which now lives in the filter row."""
    window, _repository = build_window(qtbot, tmp_path)

    buddies = [label for label in window.findChildren(main_window_module.QLabel) if label.buddy()]
    key_labels = [label for label in buddies if label.buddy() is window.key_notation_combo]

    assert len(key_labels) == 1
    assert key_labels[0].text() == main_window_module.KEY_NOTATION_LABEL
    assert not hasattr(window, "theme_combo")
    assert not hasattr(window, "status_filter_combo")
