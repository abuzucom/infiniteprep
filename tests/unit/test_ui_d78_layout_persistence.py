"""Behavioral tests for D78 table-layout persistence."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from infiniteprep.config.settings import AppSettings
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.main_window import MainWindow


class MemorySettingsManager:
    """Keep application settings in memory for window lifecycle tests."""

    def __init__(self) -> None:
        self.settings = AppSettings()

    def load(self) -> AppSettings:
        """Return a separate settings value for a new settings operation."""
        return replace(self.settings)

    def save(self, settings: AppSettings) -> None:
        """Retain a copy of the last settings value."""
        self.settings = replace(settings)


def test_file_and_detail_column_widths_persist_between_windows(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore file and detail table widths from application settings."""
    repository = Repository(DatabaseManager(":memory:"))
    review_service = ReviewService(repository)
    settings_manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: settings_manager)
    first_window = MainWindow(repository, ImportService(repository), review_service)
    qtbot.addWidget(first_window)
    first_window.file_table.horizontalHeader().resizeSection(0, 217)
    first_window.detail_panel.fields_table.horizontalHeader().resizeSection(0, 143)

    second_window = MainWindow(repository, ImportService(repository), review_service)
    qtbot.addWidget(second_window)

    assert second_window.file_table.horizontalHeader().sectionSize(0) == 217
    assert second_window.detail_panel.fields_table.horizontalHeader().sectionSize(0) == 143

    saved_layout = settings_manager.settings.file_table_layout
    settings_manager.settings.file_table_layout = ""
    settings_manager.settings.file_table_column_count = 0
    baseline_window = MainWindow(repository, ImportService(repository), review_service)
    qtbot.addWidget(baseline_window)
    default_width = baseline_window.file_table.horizontalHeader().sectionSize(0)

    settings_manager.settings.file_table_layout = saved_layout
    settings_manager.settings.file_table_column_count = 1
    third_window = MainWindow(repository, ImportService(repository), review_service)
    qtbot.addWidget(third_window)

    assert third_window.file_table.horizontalHeader().sectionSize(0) == default_width
