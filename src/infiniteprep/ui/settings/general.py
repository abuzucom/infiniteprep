"""General settings tab for application-wide choices such as the theme."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import QComboBox, QWidget

from infiniteprep.config.settings import (
    UNSORTED_PLACEMENT_FIRST,
    UNSORTED_PLACEMENT_LAST,
    AppSettings,
)
from infiniteprep.ui.settings.base import FormSettingsTab
from infiniteprep.ui.theme import SYSTEM_THEME, get_available_themes

UNSORTED_PLACEMENT_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("First", UNSORTED_PLACEMENT_FIRST),
    ("Last", UNSORTED_PLACEMENT_LAST),
)


class GeneralTab(FormSettingsTab):
    """Settings tab for the UI theme and the unsorted row placement."""

    TITLE = "General"

    def __init__(
        self,
        theme: str,
        parent: QWidget | None = None,
        *,
        unsorted_placement: str = UNSORTED_PLACEMENT_FIRST,
    ) -> None:
        """Initialize the tab with the theme and the unsorted row placement.

        The dialog resolves the theme, including the fallback for settings that
        lack one, and passes it in. The placement names where the rows without
        a value for the sorted column sit in the table.
        """
        super().__init__(parent)
        self.theme_combo = QComboBox(self)
        for theme_name in get_available_themes():
            self.theme_combo.addItem(theme_name)
        self.theme_combo.addItem(SYSTEM_THEME)
        self.theme_combo.setCurrentText(theme)
        self.form.addRow("UI Theme:", self.theme_combo)

        self.unsorted_rows_placement_combo = QComboBox(self)
        for label, value in UNSORTED_PLACEMENT_LABELS:
            self.unsorted_rows_placement_combo.addItem(label, value)
        placement_index = self.unsorted_rows_placement_combo.findData(unsorted_placement)
        if placement_index < 0:
            placement_index = self.unsorted_rows_placement_combo.findData(UNSORTED_PLACEMENT_FIRST)
        self.unsorted_rows_placement_combo.setCurrentIndex(placement_index)
        self.form.addRow("Unsorted Rows:", self.unsorted_rows_placement_combo)

    def apply_to(self, settings: AppSettings) -> None:
        """Copy the selected theme and placement into settings."""
        settings.theme = self.theme_combo.currentText()
        settings.unsorted_rows_placement = str(self.unsorted_rows_placement_combo.currentData())


TAB_CLASS = GeneralTab
