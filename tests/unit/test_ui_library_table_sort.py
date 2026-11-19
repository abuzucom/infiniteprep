"""Behavioral tests for sorting the library table (issue 248)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import KeyDisplayNotation, ReviewStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.table_sort import parse_bpm_value
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

FILENAME_COLUMN: int = 0
DURATION_COLUMN: int = 2
BPM_COLUMN: int = 7
KEY_COLUMN: int = 8
REVIEW_STATUS_COLUMN: int = 9


@dataclass(frozen=True)
class TrackSpec:
    """One library file with the tags and length the table shows."""

    name: str
    duration_sec: float = 1.0
    tags: dict[str, str] | None = None


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep the window settings in memory so no test reads the user settings file."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def build_window(
    qtbot: Any, tmp_path: Path, tracks: list[TrackSpec]
) -> tuple[MainWindow, Repository, dict[str, str]]:
    """Import the given tracks and open a window over them."""
    repo = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repo, MetadataTagAdapter())
    crate = tmp_path / "crate"
    paths = []
    for spec in tracks:
        path = crate / spec.name
        create_wav_fixture(path, duration_sec=spec.duration_sec, tags=spec.tags)
        paths.append(path)
    report = importer.import_paths(paths)
    file_ids = {Path(asset.current_path).name: asset.id for asset in report.imported_files}
    window = MainWindow(
        repository=repo, import_service=importer, review_service=ReviewService(repo)
    )
    qtbot.addWidget(window)
    return window, repo, file_ids


def click_header(window: MainWindow, column: int) -> None:
    """Click one table header section the way a user does."""
    window.file_table.horizontalHeader().sectionClicked.emit(column)


def visible_names(window: MainWindow) -> list[str]:
    """Return the file name shown in each visible row."""
    names = []
    for row in range(window.file_table.rowCount()):
        item = window.file_table.item(row, 0)
        names.append(str(item.text()))
    return names


def visible_ids(window: MainWindow) -> list[str]:
    """Return the file ID stored on the first cell of each visible row."""
    ids = []
    for row in range(window.file_table.rowCount()):
        item = window.file_table.item(row, 0)
        ids.append(str(item.data(Qt.ItemDataRole.UserRole)))
    return ids


def test_table_starts_sorted_by_filename_ascending(qtbot: Any, tmp_path: Path) -> None:
    """The table opens with the file names in ascending order."""
    window, _repo, _ids = build_window(
        qtbot, tmp_path, [TrackSpec("b.wav"), TrackSpec("a.wav"), TrackSpec("c.wav")]
    )

    assert visible_names(window) == ["a.wav", "b.wav", "c.wav"]


def test_filename_column_sorts_naturally(qtbot: Any, tmp_path: Path) -> None:
    """A number inside a name sorts by value, so Track 2 comes before Track 10."""
    window, _repo, _ids = build_window(
        qtbot, tmp_path, [TrackSpec("Track 10.wav"), TrackSpec("Track 2.wav")]
    )

    assert visible_names(window) == ["Track 2.wav", "Track 10.wav"]


def test_clicking_the_active_header_reverses_the_order(qtbot: Any, tmp_path: Path) -> None:
    """A second click on the sorted header reverses the direction."""
    window, _repo, _ids = build_window(qtbot, tmp_path, [TrackSpec("a.wav"), TrackSpec("b.wav")])

    click_header(window, FILENAME_COLUMN)

    assert visible_names(window) == ["b.wav", "a.wav"]


def test_bpm_column_sorts_numerically_in_both_directions(qtbot: Any, tmp_path: Path) -> None:
    """The BPM column sorts by value, and a second click reverses the order."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("c.wav", tags={"bpm": "210"}),
            TrackSpec("a.wav", tags={"bpm": "10"}),
            TrackSpec("b.wav", tags={"bpm": "65"}),
        ],
    )
    header = window.file_table.horizontalHeader()

    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["a.wav", "b.wav", "c.wav"]
    assert header.sortIndicatorSection() == BPM_COLUMN
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder

    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["c.wav", "b.wav", "a.wav"]
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder


def test_bpm_column_keeps_fractional_values(qtbot: Any, tmp_path: Path) -> None:
    """A fractional BPM sorts by its value and not by its text."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "128.5"}),
            TrackSpec("b.wav", tags={"bpm": "128.25"}),
        ],
    )

    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["b.wav", "a.wav"]


def test_duration_column_sorts_numerically(qtbot: Any, tmp_path: Path) -> None:
    """The duration column sorts by seconds, so a short file comes before a long one."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [TrackSpec("long.wav", duration_sec=10.0), TrackSpec("short.wav", duration_sec=1.0)],
    )

    click_header(window, DURATION_COLUMN)

    assert visible_names(window) == ["short.wav", "long.wav"]


def test_key_column_sorts_by_camelot_wheel_position(qtbot: Any, tmp_path: Path) -> None:
    """The key column sorts by wheel position in the Camelot notation."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("c.wav", tags={"key": "12B"}),
            TrackSpec("a.wav", tags={"key": "1A"}),
            TrackSpec("b.wav", tags={"key": "2B"}),
        ],
    )

    click_header(window, KEY_COLUMN)

    assert visible_names(window) == ["a.wav", "b.wav", "c.wav"]


def test_key_column_keeps_minor_before_major_at_one_position(qtbot: Any, tmp_path: Path) -> None:
    """Two keys at one wheel position keep the minor key first in both directions."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("major.wav", tags={"key": "1B"}),
            TrackSpec("minor.wav", tags={"key": "1A"}),
        ],
    )

    click_header(window, KEY_COLUMN)

    assert visible_names(window) == ["minor.wav", "major.wav"]

    click_header(window, KEY_COLUMN)

    assert visible_names(window) == ["minor.wav", "major.wav"]


def test_key_column_sorts_by_open_key_number(qtbot: Any, tmp_path: Path) -> None:
    """The key column sorts by number in the Open Key notation, minor key first."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("c.wav", tags={"key": "2m"}),
            TrackSpec("a.wav", tags={"key": "1m"}),
            TrackSpec("b.wav", tags={"key": "1d"}),
        ],
    )
    window.key_notation_combo.setCurrentText(KeyDisplayNotation.OPEN_KEY.value)

    click_header(window, KEY_COLUMN)

    assert visible_names(window) == ["a.wav", "b.wav", "c.wav"]


def test_key_column_sorts_by_pitch_class_in_standard_notation(qtbot: Any, tmp_path: Path) -> None:
    """The key column sorts upward from C in the standard notation."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("c.wav", tags={"key": "Bb"}),
            TrackSpec("a.wav", tags={"key": "C"}),
            TrackSpec("b.wav", tags={"key": "F#"}),
        ],
    )
    window.key_notation_combo.setCurrentText(KeyDisplayNotation.STANDARD.value)

    click_header(window, KEY_COLUMN)

    assert visible_names(window) == ["a.wav", "b.wav", "c.wav"]


def test_review_status_column_sorts_by_workflow_order(qtbot: Any, tmp_path: Path) -> None:
    """The review status column sorts by the workflow order."""
    window, repo, _ids = build_window(
        qtbot,
        tmp_path,
        [TrackSpec("a.wav"), TrackSpec("b.wav"), TrackSpec("c.wav")],
    )
    for name, status in (
        ("a.wav", ReviewStatus.UNPROCESSED),
        ("b.wav", ReviewStatus.PROPOSED),
        ("c.wav", ReviewStatus.WRITTEN),
    ):
        repo.update_file_status(_ids[name], review_status=status)
    window.refresh_file_table()

    click_header(window, REVIEW_STATUS_COLUMN)

    assert visible_names(window) == ["a.wav", "b.wav", "c.wav"]


def test_unsorted_rows_sort_first_by_default(qtbot: Any, tmp_path: Path) -> None:
    """A file with no BPM sorts before the files that carry one."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "128"}),
            TrackSpec("b.wav"),
        ],
    )

    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["b.wav", "a.wav"]


def test_unsorted_rows_move_with_the_direction(qtbot: Any, tmp_path: Path) -> None:
    """The unsorted group holds the lowest value, so a reversed sort puts it last."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "128"}),
            TrackSpec("b.wav"),
        ],
    )

    click_header(window, BPM_COLUMN)
    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["a.wav", "b.wav"]


def test_a_non_finite_bpm_sorts_with_the_unsorted_rows(qtbot: Any, tmp_path: Path) -> None:
    """A BPM that is not a finite number counts as no value at all."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "128"}),
            TrackSpec("b.wav", tags={"bpm": "NaN"}),
        ],
    )

    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["b.wav", "a.wav"]


def test_a_non_finite_bpm_follows_the_placement_setting(
    qtbot: Any, tmp_path: Path, settings
) -> None:
    """A BPM that is not a finite number moves with the unsorted group."""
    settings.settings = replace(settings.settings, unsorted_rows_placement="last")
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "128"}),
            TrackSpec("b.wav", tags={"bpm": "inf"}),
        ],
    )

    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["a.wav", "b.wav"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("128.5", 128.5), ("128", 128.0), ("", None), ("fast", None), ("NaN", None), ("inf", None)],
)
def test_parse_bpm_value_keeps_only_finite_numbers(text: str, expected: float | None) -> None:
    """Only a finite number becomes a BPM value."""
    assert parse_bpm_value(text) == expected


def test_clicking_a_row_after_a_sort_loads_that_file(qtbot: Any, tmp_path: Path) -> None:
    """A row click opens the file in that row, not the file at the same row index."""
    window, _repo, ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "210"}),
            TrackSpec("b.wav", tags={"bpm": "65"}),
        ],
    )

    click_header(window, BPM_COLUMN)
    window.file_table.selectRow(0)

    assert window.detail_panel.current_file_id == ids["b.wav"]


def test_refresh_keeps_the_sort_column_and_direction(qtbot: Any, tmp_path: Path) -> None:
    """A refresh keeps the sorted column and the sort direction."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "210"}),
            TrackSpec("b.wav", tags={"bpm": "65"}),
        ],
    )
    header = window.file_table.horizontalHeader()

    click_header(window, BPM_COLUMN)
    click_header(window, BPM_COLUMN)
    window.refresh_file_table()

    assert header.sortIndicatorSection() == BPM_COLUMN
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    assert visible_names(window) == ["a.wav", "b.wav"]


def test_refresh_keeps_the_selected_file(qtbot: Any, tmp_path: Path) -> None:
    """A refresh keeps the same file selected when that file stays visible."""
    window, _repo, ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "210"}),
            TrackSpec("b.wav", tags={"bpm": "65"}),
        ],
    )

    click_header(window, BPM_COLUMN)
    window.file_table.selectRow(0)
    window.refresh_file_table()

    assert window.detail_panel.current_file_id == ids["b.wav"]


def test_search_keeps_the_sort_and_the_selection(qtbot: Any, tmp_path: Path) -> None:
    """A search refresh keeps the sorted column, the direction, and the selected file."""
    window, _repo, ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("alpha.wav", tags={"bpm": "210"}),
            TrackSpec("beta.wav", tags={"bpm": "65"}),
        ],
    )
    header = window.file_table.horizontalHeader()

    click_header(window, BPM_COLUMN)
    window.file_table.selectRow(0)
    window.search_input.setText("beta")

    assert header.sortIndicatorSection() == BPM_COLUMN
    assert visible_names(window) == ["beta.wav"]
    assert window.detail_panel.current_file_id == ids["beta.wav"]


def test_visible_file_ids_follow_the_display_order(qtbot: Any, tmp_path: Path) -> None:
    """The visible file ID list matches the order shown on screen."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "210"}),
            TrackSpec("b.wav", tags={"bpm": "65"}),
        ],
    )

    click_header(window, BPM_COLUMN)

    assert window.visible_file_ids == visible_ids(window)


def test_ties_fall_back_to_the_title_then_the_path(qtbot: Any, tmp_path: Path) -> None:
    """Rows with the same value order by title, then by path."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("zebra.wav", tags={"bpm": "128", "title": "Alpha"}),
            TrackSpec("apple.wav", tags={"bpm": "128", "title": "Beta"}),
        ],
    )

    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["zebra.wav", "apple.wav"]


def test_unsorted_rows_use_the_placement_setting(qtbot: Any, tmp_path: Path, settings) -> None:
    """The placement setting decides where the unsorted group sits."""
    settings.settings = replace(settings.settings, unsorted_rows_placement="last")
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "128"}),
            TrackSpec("b.wav"),
        ],
    )

    click_header(window, BPM_COLUMN)

    assert visible_names(window) == ["a.wav", "b.wav"]


def test_a_changed_placement_moves_the_unsorted_rows(qtbot: Any, tmp_path: Path, settings) -> None:
    """The window reads the placement again and repaints the table."""
    window, _repo, _ids = build_window(
        qtbot,
        tmp_path,
        [
            TrackSpec("a.wav", tags={"bpm": "128"}),
            TrackSpec("b.wav"),
        ],
    )

    click_header(window, BPM_COLUMN)
    settings.settings = replace(settings.settings, unsorted_rows_placement="last")
    window.reload_settings()

    assert visible_names(window) == ["a.wav", "b.wav"]


def test_header_keeps_the_sort_indicator_shown(qtbot: Any, tmp_path: Path) -> None:
    """The header shows the sort indicator for the active column."""
    window, _repo, _ids = build_window(qtbot, tmp_path, [TrackSpec("a.wav")])
    header = window.file_table.horizontalHeader()

    assert isinstance(header, QHeaderView)
    assert header.isSortIndicatorShown() is True
    assert header.sortIndicatorSection() == FILENAME_COLUMN
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
