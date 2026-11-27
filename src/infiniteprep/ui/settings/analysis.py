"""Analysis settings tab, including the batch stop rule and the worker count."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import QCheckBox, QComboBox, QSpinBox, QWidget

from infiniteprep.config.settings import (
    MAX_ANALYSIS_WORKERS,
    MIN_ANALYSIS_WORKERS,
    AppSettings,
)
from infiniteprep.services.fake_lossless import available_methods
from infiniteprep.ui.settings.base import FormSettingsTab

STOP_ON_FAILURE_LABEL: Final[str] = "Stop when a file fails"
WORKER_COUNT_LABEL: Final[str] = "Analysis workers:"
WORKER_COUNT_TOOLTIP: Final[str] = (
    "How many tracks the app analyzes at the same time. The count never passes the "
    "number of processor cores."
)


class AnalysisTab(FormSettingsTab):
    """Settings tab for analysis and the batch queue."""

    TITLE = "Analysis"
    ICON = "microscope"
    TAKES_EFFECT = (
        "Changes apply to your next Analyze or write batch, and to the next track you pick."
    )

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        """Initialize the tab from the stored settings."""
        super().__init__(parent)
        self.stop_on_failure_checkbox = QCheckBox(STOP_ON_FAILURE_LABEL, self)
        self.stop_on_failure_checkbox.setChecked(settings.stop_batch_on_first_failure)
        self.form.addRow("Queue:", self.stop_on_failure_checkbox)

        self.worker_count_spin = QSpinBox(self)
        self.worker_count_spin.setRange(MIN_ANALYSIS_WORKERS, MAX_ANALYSIS_WORKERS)
        self.worker_count_spin.setValue(settings.analysis_workers)
        self.worker_count_spin.setToolTip(WORKER_COUNT_TOOLTIP)
        self.form.addRow(WORKER_COUNT_LABEL, self.worker_count_spin)

        self.fake_lossless_method_combo = QComboBox(self)
        for method in available_methods():
            self.fake_lossless_method_combo.addItem(method)
        self.fake_lossless_method_combo.setCurrentText(settings.fake_lossless_method)
        self.form.addRow("Fake lossless check:", self.fake_lossless_method_combo)

    def apply_to(self, settings: AppSettings) -> None:
        """Copy the batch stop rule, worker count, and fake-lossless method into settings."""
        settings.stop_batch_on_first_failure = self.stop_on_failure_checkbox.isChecked()
        settings.analysis_workers = self.worker_count_spin.value()
        settings.fake_lossless_method = self.fake_lossless_method_combo.currentText()


TAB_CLASS = AnalysisTab
