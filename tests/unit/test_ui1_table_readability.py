"""Library and detail table widths, readable names, and warning summaries."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QHeaderView, QPushButton, QTableWidget

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, KeyDisplayNotation
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.detail_panel import (
    COLUMN_ACTIONS,
    COLUMN_APPROVED_EDIT,
    COLUMN_EXISTING_TAG,
    COLUMN_FIELD_NAME,
    COLUMN_TOP_PROPOSAL,
    FIELD_LABELS,
    ROW_FIELD_PROPERTY,
    DetailPanel,
)
from infiniteprep.ui.main_window import (
    KEY_NOTATION_LABELS,
    TABLE_COLUMNS,
    MainWindow,
    format_warning_summary,
)
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager
from tests.fixtures.waveform_stub import keep_waveform_pool_idle

WINDOW_HEIGHT = 820
WINDOW_WIDTHS = (1024, 1360, 1920)
# The narrowest window whose table area, beside the sidebar and the 480 px panel,
# reaches the 1100 px that shows every column.
WIDE_START_WIDTH = 1850
FILENAME_COLUMN = 0
ARTIST_COLUMN = 3
TITLE_COLUMN = 4
MIX_COLUMN = 5
LABEL_COLUMN = 6
WARNINGS_COLUMN = 10
LARGE_SHARE_COLUMNS = (FILENAME_COLUMN, ARTIST_COLUMN, TITLE_COLUMN)
SMALL_SHARE_COLUMNS = (MIX_COLUMN, LABEL_COLUMN)
# Integer division of the spare width leaves each column up to one pixel short.
ROUNDING_SLACK_PX = 2
DRAGGED_WIDTH = 333
HIGH_SAMPLE_RATE = 96_000
PANEL_WIDTH = 1400
PANEL_HEIGHT = 900


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep the window settings in memory so no test reads the user settings file."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def import_library(tmp_path: Path, sample_rate: int = 44_100) -> tuple[Repository, ImportService]:
    """Import two WAV files into an in-memory library."""
    repo = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repo, MetadataTagAdapter())
    paths = [
        create_wav_fixture(tmp_path / "crate" / name, duration_sec=0.5, sample_rate=sample_rate)
        for name in ("first.wav", "second.wav")
    ]
    importer.import_paths(paths)
    return repo, importer


def open_window(qtbot: Any, tmp_path: Path, width: int, sample_rate: int = 44_100) -> MainWindow:
    """Show a main window over a small library at the given width."""
    repo, importer = import_library(tmp_path, sample_rate)
    window = MainWindow(
        repository=repo, import_service=importer, review_service=ReviewService(repo)
    )
    qtbot.addWidget(window)
    window.resize(width, WINDOW_HEIGHT)
    window.show()
    qtbot.waitExposed(window)
    QApplication.processEvents()
    return window


def section_widths(table: QTableWidget) -> list[int]:
    """Return the width of every header section."""
    header = table.horizontalHeader()
    return [header.sectionSize(index) for index in range(header.count())]


def test_format_warning_summary_counts_warnings() -> None:
    """The Issues cell shows a count, and nothing when a file has no issues."""
    assert format_warning_summary([]) == ""
    assert format_warning_summary(["clipping"]) == "1 issue"
    assert format_warning_summary(["a", "b", "c"]) == "3 issues"


def test_library_table_no_last_stretch(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """No column absorbs the spare width through the last-section stretch."""
    window = open_window(qtbot, tmp_path, WINDOW_WIDTHS[0])

    assert not window.file_table.horizontalHeader().stretchLastSection()


@pytest.mark.parametrize("width", WINDOW_WIDTHS)
def test_equal_stretch_shares(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager, width: int
) -> None:
    """Each shown stretching column (Filename, Artist, Title) gets an equal share."""
    window = open_window(qtbot, tmp_path, width)
    header = window.file_table.horizontalHeader()
    widths = section_widths(window.file_table)
    shares = [
        widths[column] for column in LARGE_SHARE_COLUMNS if not header.isSectionHidden(column)
    ]

    assert len(shares) >= len(LARGE_SHARE_COLUMNS) - 1
    assert max(shares) - min(shares) <= ROUNDING_SLACK_PX
    assert widths[WARNINGS_COLUMN] < widths[ARTIST_COLUMN]


@pytest.mark.parametrize("width", WINDOW_WIDTHS)
def test_columns_fill_viewport_without_overflow(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager, width: int
) -> None:
    """The weighted shares replace the old widths, so the columns sum to the viewport width.

    Each weighted share rounds down, which leaves at most one pixel per weighted column.
    """
    window = open_window(qtbot, tmp_path, width)
    total = sum(section_widths(window.file_table))
    viewport_width = window.file_table.viewport().width()
    weighted_count = len(LARGE_SHARE_COLUMNS) + len(SMALL_SHARE_COLUMNS)

    assert viewport_width - weighted_count <= total <= viewport_width


def test_wider_window_widens_named_columns(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A wider window widens the weighted columns and leaves the fitted columns as they were.

    Both widths use the wide layout with every column shown, so only the spare width
    changes between them.
    """
    window = open_window(qtbot, tmp_path, WIDE_START_WIDTH)
    narrow = section_widths(window.file_table)

    window.resize(WINDOW_WIDTHS[-1], WINDOW_HEIGHT)
    QApplication.processEvents()
    wide = section_widths(window.file_table)

    assert wide[ARTIST_COLUMN] > narrow[ARTIST_COLUMN]
    assert wide[WARNINGS_COLUMN] == narrow[WARNINGS_COLUMN]


def test_dragged_column_keeps_session_width(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A width the user sets survives a window resize."""
    window = open_window(qtbot, tmp_path, WINDOW_WIDTHS[1])
    window.file_table.horizontalHeader().resizeSection(ARTIST_COLUMN, DRAGGED_WIDTH)

    window.resize(WINDOW_WIDTHS[-1], WINDOW_HEIGHT)
    QApplication.processEvents()

    assert window.file_table.horizontalHeader().sectionSize(ARTIST_COLUMN) == DRAGGED_WIDTH


def test_restored_layout_no_last_stretch(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A layout saved by an older version, with the last-section stretch on, loads without it."""
    old_table = QTableWidget(0, len(TABLE_COLUMNS))
    qtbot.addWidget(old_table)
    old_table.horizontalHeader().setStretchLastSection(True)
    saved_state = bytes(old_table.horizontalHeader().saveState())
    settings.settings.file_table_layout = base64.b64encode(saved_state).decode("ascii")
    settings.settings.file_table_column_count = len(TABLE_COLUMNS)

    window = open_window(qtbot, tmp_path, WINDOW_WIDTHS[1])
    header = window.file_table.horizontalHeader()

    assert not header.stretchLastSection()
    assert header.sectionResizeMode(WARNINGS_COLUMN) == QHeaderView.ResizeMode.Interactive


def test_headers_carry_full_text_tooltip(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """An elided header still shows its full name on hover."""
    window = open_window(qtbot, tmp_path, WINDOW_WIDTHS[0])

    tooltips = [
        window.file_table.horizontalHeaderItem(index).toolTip()
        for index in range(len(TABLE_COLUMNS))
    ]
    assert tooltips == list(TABLE_COLUMNS)


def test_warning_cell_shows_count_tooltip(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A file above the player sample-rate limit shows a count, with the warnings on hover."""
    window = open_window(qtbot, tmp_path, WINDOW_WIDTHS[1], sample_rate=HIGH_SAMPLE_RATE)
    asset = window.repo.list_files()[0]
    row = window.visible_file_ids.index(asset.id)
    snapshot = main_window_module.load_library_snapshot(window.repo)
    warnings = MainWindow._asset_warnings(asset, snapshot)
    item = window.file_table.item(row, WARNINGS_COLUMN)

    assert warnings
    assert item.text() == format_warning_summary(warnings)
    assert item.toolTip() == "\n".join(warnings)


def test_search_matches_visible_warning_cell(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """Warning text sits in a tooltip, so typing it no longer matches the row."""
    window = open_window(qtbot, tmp_path, WINDOW_WIDTHS[1], sample_rate=HIGH_SAMPLE_RATE)
    asset = window.repo.list_files()[0]
    snapshot = main_window_module.load_library_snapshot(window.repo)
    first_warning = MainWindow._asset_warnings(asset, snapshot)[0]

    window.search_input.setText(first_warning)

    assert window.file_table.rowCount() == 0


def test_key_notation_shows_readable_names(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """The box lists readable names and keeps the stored notation values as item data."""
    window = open_window(qtbot, tmp_path, WINDOW_WIDTHS[0])
    combo = window.key_notation_combo

    shown = [combo.itemText(index) for index in range(combo.count())]
    stored = [combo.itemData(index) for index in range(combo.count())]
    assert shown == [KEY_NOTATION_LABELS[notation] for notation in KeyDisplayNotation]
    assert stored == [notation.value for notation in KeyDisplayNotation]
    assert KEY_NOTATION_LABELS[KeyDisplayNotation.OPEN_KEY] == "Open Key"

    combo.setCurrentIndex(combo.findData(KeyDisplayNotation.OPEN_KEY.value))

    assert window.current_key_notation == KeyDisplayNotation.OPEN_KEY
    assert window.detail_panel.key_notation == KeyDisplayNotation.OPEN_KEY


@pytest.fixture
def shown_panel(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DetailPanel:
    """Show a detail panel with one imported file loaded."""
    keep_waveform_pool_idle(monkeypatch)
    repo, _importer = import_library(tmp_path)
    panel = DetailPanel(repo, ReviewService(repo), settings_manager=MemorySettingsManager())
    qtbot.addWidget(panel)
    panel.resize(PANEL_WIDTH, PANEL_HEIGHT)
    panel.show()
    qtbot.waitExposed(panel)
    panel.load_file_asset(repo.list_files()[0].id)
    QApplication.processEvents()
    return panel


def test_field_column_prefers_readable_names(shown_panel: DetailPanel) -> None:
    """Rows show names such as "Mix name" and their controls keep the stored field keys."""
    table = shown_panel.fields_table
    shown = [table.item(row, COLUMN_FIELD_NAME).text() for row in range(table.rowCount())]
    keys = [
        table.cellWidget(row, COLUMN_APPROVED_EDIT).property(ROW_FIELD_PROPERTY)
        for row in range(table.rowCount())
    ]

    assert shown == [FIELD_LABELS[field.value] for field in FieldName]
    assert keys == [field.value for field in FieldName]
    assert FIELD_LABELS[FieldName.MIX_NAME.value] == "Mix name"
    assert FIELD_LABELS[FieldName.CATALOG_NUMBER.value] == "Catalog number"
    assert FIELD_LABELS[FieldName.BPM.value] == "BPM"


def test_detail_columns_share_spare_width(shown_panel: DetailPanel) -> None:
    """Existing Tag, Top Proposal, and Approved / Edit share the spare width equally."""
    header = shown_panel.fields_table.horizontalHeader()
    shares = [
        header.sectionSize(column)
        for column in (COLUMN_EXISTING_TAG, COLUMN_TOP_PROPOSAL, COLUMN_APPROVED_EDIT)
    ]

    assert not header.stretchLastSection()
    assert max(shares) - min(shares) <= ROUNDING_SLACK_PX
    assert shares[0] > header.sectionSize(COLUMN_FIELD_NAME)


def test_actions_column_fits_its_buttons(shown_panel: DetailPanel) -> None:
    """The Actions column fits its buttons, and each button keeps its natural width."""
    table = shown_panel.fields_table
    fitted = table.sizeHintForColumn(COLUMN_ACTIONS)

    assert table.horizontalHeader().sectionSize(COLUMN_ACTIONS) == fitted
    for button in table.cellWidget(0, COLUMN_ACTIONS).findChildren(QPushButton):
        assert button.width() <= button.sizeHint().width()
