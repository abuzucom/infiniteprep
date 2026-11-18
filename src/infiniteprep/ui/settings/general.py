"""General settings tab for application-wide choices such as the theme."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import QCheckBox, QComboBox, QWidget

from infiniteprep.config.settings import (
    UNSORTED_PLACEMENT_FIRST,
    UNSORTED_PLACEMENT_LAST,
    AppSettings,
)
from infiniteprep.domain.enums import KeyDisplayNotation
from infiniteprep.domain.tag_output import BPM_DECIMAL_PLACES
from infiniteprep.ui.settings.base import FormSettingsTab
from infiniteprep.ui.theme import fill_theme_picker, get_theme_choices, select_theme

UNSORTED_PLACEMENT_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("First", UNSORTED_PLACEMENT_FIRST),
    ("Last", UNSORTED_PLACEMENT_LAST),
)


class GeneralTab(FormSettingsTab):
    """Settings tab for the UI theme, unsorted rows, and tag output."""

    TITLE = "General"

    def __init__(
        self,
        theme: str,
        parent: QWidget | None = None,
        *,
        unsorted_placement: str = UNSORTED_PLACEMENT_FIRST,
        settings: AppSettings | None = None,
    ) -> None:
        """Initialize the tab with the theme, row placement, and tag output.

        The dialog resolves the theme, including the fallback for settings that
        lack one, and passes it in. The placement names where the rows without
        a value for the sorted column sit in the table.
        """
        super().__init__(parent)
        self.theme_combo = QComboBox(self)
        fill_theme_picker(self.theme_combo, get_theme_choices())
        select_theme(self.theme_combo, theme)
        self.form.addRow("UI Theme:", self.theme_combo)
        self.unsorted_rows_placement_combo = QComboBox(self)
        for label, value in UNSORTED_PLACEMENT_LABELS:
            self.unsorted_rows_placement_combo.addItem(label, value)
        placement_index = self.unsorted_rows_placement_combo.findData(unsorted_placement)
        if placement_index < 0:
            placement_index = self.unsorted_rows_placement_combo.findData(UNSORTED_PLACEMENT_FIRST)
        self.unsorted_rows_placement_combo.setCurrentIndex(placement_index)
        self.form.addRow("Unsorted Rows:", self.unsorted_rows_placement_combo)
        self._add_tag_output_controls(settings or AppSettings())

    def _add_tag_output_controls(self, settings: AppSettings) -> None:
        """Add a tag format independent of the key notation used in the library."""
        self.tag_key_notation_combo = QComboBox(self)
        self.tag_key_notation_combo.addItem("Camelot", KeyDisplayNotation.CAMELOT.value)
        self.tag_key_notation_combo.addItem("Open Key", KeyDisplayNotation.OPEN_KEY.value)
        self.tag_key_notation_combo.addItem("Standard", KeyDisplayNotation.STANDARD.value)
        self.tag_key_notation_combo.setCurrentIndex(
            self.tag_key_notation_combo.findData(settings.tag_key_notation)
        )
        self.form.addRow("Written Key Notation:", self.tag_key_notation_combo)

        self.tag_key_leading_zero_checkbox = QCheckBox("Pad Camelot and Open Key with a zero", self)
        self.tag_key_leading_zero_checkbox.setChecked(settings.tag_key_leading_zero)
        self.form.addRow(self.tag_key_leading_zero_checkbox)

        self.tag_bpm_decimals_combo = QComboBox(self)
        labels = ("Integer", "1 decimal", "2 decimals")
        for decimals, label in zip(BPM_DECIMAL_PLACES, labels, strict=True):
            self.tag_bpm_decimals_combo.addItem(label, decimals)
        self.tag_bpm_decimals_combo.setCurrentIndex(
            self.tag_bpm_decimals_combo.findData(settings.tag_bpm_decimals)
        )
        self.form.addRow("Written BPM Precision:", self.tag_bpm_decimals_combo)

    def set_theme(self, theme: str) -> None:
        """Point the theme picker at the given theme."""
        select_theme(self.theme_combo, theme)

    def apply_to(self, settings: AppSettings) -> None:
        """Copy theme, sorting, and tag output preferences into settings."""
        settings.theme = str(self.theme_combo.currentData())
        settings.unsorted_rows_placement = str(self.unsorted_rows_placement_combo.currentData())
        settings.tag_key_notation = self.tag_key_notation_combo.currentData()
        settings.tag_key_leading_zero = self.tag_key_leading_zero_checkbox.isChecked()
        settings.tag_bpm_decimals = self.tag_bpm_decimals_combo.currentData()


TAB_CLASS = GeneralTab
