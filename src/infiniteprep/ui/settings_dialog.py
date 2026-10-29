"""Provider credentials, consent-gated binary setup, and transcode settings dialog."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.adapters.llm.ollama_openai import RECOMMENDED_MODELS
from infiniteprep.adapters.transcoder.safe_transcoder import TranscodeMode, TranscodePreset
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import AppSettings, SettingsManager

SETTINGS_DIALOG_WIDTH: Final[int] = 620
SETTINGS_DIALOG_HEIGHT: Final[int] = 460


class SettingsDialog(QDialog):
    """Modal dialog for managing API keys, Ollama/fpcalc setup, and XDJ-RX3 transcode options."""

    def __init__(
        self,
        settings_manager: SettingsManager,
        credential_store: CredentialStore,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize SettingsDialog with SettingsManager and CredentialStore."""
        super().__init__(parent)
        self.settings_mgr = settings_manager
        self.creds = credential_store
        self.current_settings: AppSettings = self.settings_mgr.load()

        self.setWindowTitle("InfinitePrep Settings & Providers")
        self.resize(SETTINGS_DIALOG_WIDTH, SETTINGS_DIALOG_HEIGHT)

        self.acoustid_key_input = QLineEdit(self.creds.get_secret("acoustid_api_key") or "", self)
        self.discogs_token_input = QLineEdit(self.creds.get_secret("discogs_token") or "", self)
        self.enable_llm_checkbox = QCheckBox("Enable Closed-World LLM Arbitration", self)
        self.ollama_url_input = QLineEdit(self.current_settings.ollama_base_url, self)
        self.ollama_model_combo = QComboBox(self)
        self.transcode_mode_combo = QComboBox(self)
        self.transcode_preset_combo = QComboBox(self)
        self.save_btn = QPushButton("Save Settings", self)
        self.cancel_btn = QPushButton("Cancel", self)

        self._build_layout()

    def _build_layout(self) -> None:
        """Construct the settings form layout."""
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.acoustid_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.discogs_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("AcoustID API Key:", self.acoustid_key_input)
        form.addRow("Discogs Personal Token:", self.discogs_token_input)

        self.enable_llm_checkbox.setChecked(self.current_settings.enable_llm_arbitration)
        form.addRow("Optional Local LLM:", self.enable_llm_checkbox)
        form.addRow("Ollama / OpenAI Base URL:", self.ollama_url_input)

        self.ollama_model_combo.setEditable(True)
        for model in RECOMMENDED_MODELS:
            self.ollama_model_combo.addItem(model)
        self.ollama_model_combo.setCurrentText(self.current_settings.ollama_model)
        form.addRow("Ollama Model:", self.ollama_model_combo)

        for mode in TranscodeMode:
            self.transcode_mode_combo.addItem(mode.value)
        self.transcode_mode_combo.setCurrentText(self.current_settings.transcode_mode)
        form.addRow("XDJ-RX3 Transcode Mode:", self.transcode_mode_combo)

        for preset in TranscodePreset:
            self.transcode_preset_combo.addItem(preset.value)
        self.transcode_preset_combo.setCurrentText(self.current_settings.transcode_preset)
        form.addRow("XDJ-RX3 Transcode Preset:", self.transcode_preset_combo)

        root.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)

        self.save_btn.clicked.connect(self.save_settings)
        self.cancel_btn.clicked.connect(self.reject)

    def save_settings(self) -> None:
        """Persist updated settings to JSON and secrets to CredentialStore."""
        if self.acoustid_key_input.text().strip():
            self.creds.set_secret("acoustid_api_key", self.acoustid_key_input.text().strip())
        if self.discogs_token_input.text().strip():
            self.creds.set_secret("discogs_token", self.discogs_token_input.text().strip())

        self.current_settings.enable_llm_arbitration = self.enable_llm_checkbox.isChecked()
        self.current_settings.ollama_base_url = self.ollama_url_input.text().strip()
        self.current_settings.ollama_model = self.ollama_model_combo.currentText().strip()
        self.current_settings.transcode_mode = self.transcode_mode_combo.currentText()
        self.current_settings.transcode_preset = self.transcode_preset_combo.currentText()
        self.settings_mgr.save(self.current_settings)
        self.accept()
