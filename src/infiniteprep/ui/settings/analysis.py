"""Analysis settings tab, including the batch stop rule."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import QCheckBox, QWidget

from infiniteprep.config.settings import AppSettings
from infiniteprep.ui.settings.base import FormSettingsTab

STOP_ON_FAILURE_LABEL: Final[str] = "Stop an Analyze or write batch at its first failed file"


class AnalysisTab(FormSettingsTab):
    """Settings tab for analysis and the batch queue."""

    TITLE = "Analysis"

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        """Initialize the tab from the stored settings."""
        super().__init__(parent)
        self.stop_on_failure_checkbox = QCheckBox(STOP_ON_FAILURE_LABEL, self)
        self.stop_on_failure_checkbox.setChecked(settings.stop_batch_on_first_failure)
        self.form.addRow("Batch Queue:", self.stop_on_failure_checkbox)

    def apply_to(self, settings: AppSettings) -> None:
        """Copy the batch stop rule into settings."""
        settings.stop_batch_on_first_failure = self.stop_on_failure_checkbox.isChecked()


TAB_CLASS = AnalysisTab
