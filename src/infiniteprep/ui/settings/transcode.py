"""Transcode settings tab for XDJ output and playlist export paths."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QWidget

from infiniteprep.adapters.transcoder.safe_transcoder import TranscodeMode, TranscodePreset
from infiniteprep.config.settings import AppSettings
from infiniteprep.ui.settings.base import FormSettingsTab


class TranscodeTab(FormSettingsTab):
    """Settings tab for the transcode mode, the preset, and playlist path style."""

    TITLE = "Transcode"
    ICON = "arrows-exchange"
    TAKES_EFFECT = "Changes apply to your next transcode and your next playlist export."

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        """Initialize the tab from the stored settings."""
        super().__init__(parent)
        self.transcode_mode_combo = QComboBox(self)
        for mode in TranscodeMode:
            self.transcode_mode_combo.addItem(mode.value)
        self.transcode_mode_combo.setCurrentText(settings.transcode_mode)
        self.form.addRow("XDJ Transcode Mode:", self.transcode_mode_combo)

        self.transcode_preset_combo = QComboBox(self)
        for preset in TranscodePreset:
            self.transcode_preset_combo.addItem(preset.value)
        self.transcode_preset_combo.setCurrentText(settings.transcode_preset)
        self.form.addRow("XDJ Transcode Preset:", self.transcode_preset_combo)

        self.playlist_relative_checkbox = QCheckBox("Write paths relative to the playlist", self)
        self.playlist_relative_checkbox.setChecked(settings.playlist_relative_paths)
        self.form.addRow("Playlist Export:", self.playlist_relative_checkbox)

    def apply_to(self, settings: AppSettings) -> None:
        """Copy the transcode and playlist export inputs into settings."""
        settings.transcode_mode = self.transcode_mode_combo.currentText()
        settings.transcode_preset = self.transcode_preset_combo.currentText()
        settings.playlist_relative_paths = self.playlist_relative_checkbox.isChecked()


TAB_CLASS = TranscodeTab
