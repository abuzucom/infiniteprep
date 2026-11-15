"""General settings tab for application-wide choices such as the theme."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QWidget

from infiniteprep.config.settings import AppSettings
from infiniteprep.ui.settings.base import FormSettingsTab
from infiniteprep.ui.theme import SYSTEM_THEME, get_available_themes


class GeneralTab(FormSettingsTab):
    """Settings tab for the UI theme."""

    TITLE = "General"

    def __init__(self, theme: str, parent: QWidget | None = None) -> None:
        """Initialize the tab with the theme to select.

        The dialog resolves the theme, including the fallback for settings that
        lack one, and passes it in.
        """
        super().__init__(parent)
        self.theme_combo = QComboBox(self)
        for theme_name in get_available_themes():
            self.theme_combo.addItem(theme_name)
        self.theme_combo.addItem(SYSTEM_THEME)
        self.theme_combo.setCurrentText(theme)
        self.form.addRow("UI Theme:", self.theme_combo)

    def apply_to(self, settings: AppSettings) -> None:
        """Copy the selected theme into settings."""
        settings.theme = self.theme_combo.currentText()


TAB_CLASS = GeneralTab
