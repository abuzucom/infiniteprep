"""Tests that hiding and showing columns keeps the sizer's shares and pins correct."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTableWidget

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.column_sizing import WeightedColumnSizer
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.memory_repository import build_memory_repository

COLUMN_COUNT = 4
WEIGHTED_COLUMNS = (0, 1, 2)
FITTED_COLUMN = 3
MINIMUM_SHARE = 40
TABLE_WIDTH = 900
TABLE_HEIGHT = 300
ROUNDING_SLACK_PX = 2


def _build_sized_table(qtbot: Any) -> tuple[QTableWidget, WeightedColumnSizer]:
    """Return a shown table whose three first columns share the spare width."""
    table = QTableWidget(0, COLUMN_COUNT)
    qtbot.addWidget(table)
    sizer = WeightedColumnSizer(table, dict.fromkeys(WEIGHTED_COLUMNS, 1), MINIMUM_SHARE)
    table.resize(TABLE_WIDTH, TABLE_HEIGHT)
    table.show()
    qtbot.waitExposed(table)
    sizer.refit()
    return table, sizer


def test_hide_and_show_pin_nothing(qtbot: Any) -> None:
    """A column that hides and shows again keeps taking its share."""
    table, sizer = _build_sized_table(qtbot)
    header = table.horizontalHeader()

    header.setSectionHidden(0, True)
    header.setSectionHidden(0, False)

    assert not sizer.is_pinned(0)
    shares = [header.sectionSize(column) for column in WEIGHTED_COLUMNS]
    assert max(shares) - min(shares) <= ROUNDING_SLACK_PX


def test_hidden_column_frees_its_share(qtbot: Any) -> None:
    """The columns that stay shown take the width a hidden column leaves."""
    table, _sizer = _build_sized_table(qtbot)
    header = table.horizontalHeader()
    shown_before = header.sectionSize(1)

    header.setSectionHidden(0, True)

    assert header.sectionSize(1) > shown_before
    assert header.sectionSize(1) - header.sectionSize(2) <= ROUNDING_SLACK_PX


def test_drag_still_pins_a_column(qtbot: Any) -> None:
    """A width the user sets stays set."""
    table, sizer = _build_sized_table(qtbot)

    table.horizontalHeader().resizeSection(FITTED_COLUMN, MINIMUM_SHARE * 3)

    assert sizer.is_pinned(FITTED_COLUMN)


def test_unshown_window_hides_nothing(qtbot: Any) -> None:
    """The width rules wait for a real table width, so a new window hides no column."""
    repository = build_memory_repository()
    importer = ImportService(repository, MetadataTagAdapter())
    window = MainWindow(repository, importer, ReviewService(repository))
    qtbot.addWidget(window)
    header = window.file_table.horizontalHeader()

    assert [column for column in range(header.count()) if header.isSectionHidden(column)] == []
