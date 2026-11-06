"""Behavioral tests for D78 time displays and resizable table headers."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QHeaderView

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.audio_player import format_clock
from infiniteprep.ui.detail_panel import DetailPanel
from infiniteprep.ui.journal_dialog import JournalDialog
from infiniteprep.ui.main_window import MainWindow, format_duration
from infiniteprep.ui.recovery_dialog import RecoveryDialog
from infiniteprep.ui.write_plan_dialog import WritePlanDialog


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0:00"),
        (8 * 60 + 37.9, "8:37"),
        (59 * 60 + 59.99, "59:59"),
        (60 * 60, "1:00:00"),
        (60 * 60 + 125.9, "1:02:05"),
    ],
)
def test_format_duration_uses_whole_seconds(seconds: float, expected: str) -> None:
    """Duration rows truncate fractions and switch to an hour field at one hour."""
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (8 * 60 + 37.25, "8:37.250"),
        (59 * 60 + 59.999, "59:59.999"),
        (59 * 60 + 59.9995, "59:59.999"),
        (60 * 60 + 125.25, "1:02:05.250"),
    ],
)
def test_format_clock_shows_milliseconds(seconds: float, expected: str) -> None:
    """Player clocks show milliseconds and add an hour field when needed."""
    assert format_clock(seconds) == expected


def test_tables_allow_column_resizing_and_stretch_the_last_section(
    qtbot: Any,
) -> None:
    """All D78 tables allow manual resizing while filling the final column."""
    repository = Repository(DatabaseManager(":memory:"))
    review_service = ReviewService(repository)
    write_service = WriteService(repository)
    main_window = MainWindow(repository, ImportService(repository), review_service)
    detail_panel = DetailPanel(repository, review_service)
    journal_dialog = JournalDialog(repository, write_service)
    write_plan_dialog = WritePlanDialog(write_service, [])
    recovery_dialog = RecoveryDialog(write_service)
    widgets = [main_window, detail_panel, journal_dialog, write_plan_dialog, recovery_dialog]

    for widget in widgets:
        qtbot.addWidget(widget)

    tables = [
        main_window.file_table,
        detail_panel.fields_table,
        journal_dialog.table,
        write_plan_dialog.table,
        recovery_dialog.table,
    ]
    for table in tables:
        header = table.horizontalHeader()
        assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
        assert header.stretchLastSection()
