"""Shared base classes for Settings dialog tabs."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget

from infiniteprep.config.settings import AppSettings


class SettingsTab(QWidget):
    """One Settings dialog tab. It reads its settings at build time and writes each on save."""

    TITLE: ClassVar[str] = ""

    def apply_to(self, settings: AppSettings) -> None:
        """Copy this tab's input values into settings. A tab without settings changes nothing."""


class FormSettingsTab(SettingsTab):
    """A tab that lays out its settings as labeled form rows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the tab page with an empty form layout for its rows."""
        super().__init__(parent)
        self.form = QFormLayout(self)


class PlaceholderTab(SettingsTab):
    """A tab for an area with no settings yet. It shows a one-line note."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the tab with a note naming its empty area."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"No {self.TITLE.lower()} settings yet.", self))
        layout.addStretch()
