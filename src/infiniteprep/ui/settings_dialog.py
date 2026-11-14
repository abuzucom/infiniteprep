"""Provider credentials, consent-gated binary setup, and transcode settings dialog."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from infiniteprep import storage_paths
from infiniteprep.adapters.llm.ollama_openai import RECOMMENDED_MODELS
from infiniteprep.adapters.transcoder.safe_transcoder import TranscodeMode, TranscodePreset
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import (
    BYTES_PER_MIB,
    MAX_USB_RESERVE_MIB,
    MAX_USB_RESERVE_PERCENT,
    AppSettings,
    SettingsManager,
)
from infiniteprep.data_cleanup import CleanupChoice, data_folders
from infiniteprep.services.fake_lossless import available_methods
from infiniteprep.storage_paths import StorageLocations
from infiniteprep.ui.cleanup_dialog import CleanupDialog
from infiniteprep.ui.theme import DEFAULT_DARK_THEME, apply_application_theme, get_available_themes

SETTINGS_DIALOG_WIDTH: Final[int] = 620
SETTINGS_DIALOG_HEIGHT: Final[int] = 460
CLEANUP_BUTTON_LABEL: Final[str] = "Delete InfinitePrep Data..."
USB_RESERVE_PERCENT_DECIMALS: Final[int] = 1
STOP_ON_FAILURE_LABEL: Final[str] = "Stop an Analyze or write batch at its first failed file"


class SettingsDialog(QDialog):
    """Modal dialog for managing API keys, Ollama/fpcalc setup, and XDJ-RX3 transcode options."""

    def __init__(
        self,
        settings_manager: SettingsManager,
        credential_store: CredentialStore,
        parent: QWidget | None = None,
        storage_locations: StorageLocations | None = None,
        cleanup_enabled: bool = True,
    ) -> None:
        """Initialize SettingsDialog with SettingsManager and CredentialStore.

        storage_locations names the folders the cleanup action lists. It defaults to the
        locations chosen at startup. A false cleanup_enabled disables the cleanup
        button, for use while a batch runs or waits.
        """
        super().__init__(parent)
        self.settings_mgr = settings_manager
        self.creds = credential_store
        self.storage_locations = storage_locations or storage_paths.current()
        # Set when the person confirms the cleanup. The caller then runs it (D295).
        self.cleanup_choice: CleanupChoice | None = None
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
        self.fake_lossless_method_combo = QComboBox(self)
        self.playlist_relative_checkbox = QCheckBox("Write paths relative to the playlist", self)
        self.stop_on_failure_checkbox = QCheckBox(STOP_ON_FAILURE_LABEL, self)
        self.theme_combo = QComboBox(self)
        self.usb_reserve_percent_spin = QDoubleSpinBox(self)
        self.usb_reserve_mib_spin = QSpinBox(self)
        self.save_btn = QPushButton("Save Settings", self)
        self.cancel_btn = QPushButton("Cancel", self)
        self.cleanup_btn = QPushButton(CLEANUP_BUTTON_LABEL, self)
        self.cleanup_btn.setEnabled(cleanup_enabled)

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
        form.addRow("XDJ Transcode Mode:", self.transcode_mode_combo)

        for preset in TranscodePreset:
            self.transcode_preset_combo.addItem(preset.value)
        self.transcode_preset_combo.setCurrentText(self.current_settings.transcode_preset)
        form.addRow("XDJ Transcode Preset:", self.transcode_preset_combo)

        for method in available_methods():
            self.fake_lossless_method_combo.addItem(method)
        self.fake_lossless_method_combo.setCurrentText(self.current_settings.fake_lossless_method)
        form.addRow("Fake-Lossless Method:", self.fake_lossless_method_combo)

        self.playlist_relative_checkbox.setChecked(self.current_settings.playlist_relative_paths)
        form.addRow("Playlist Export:", self.playlist_relative_checkbox)

        self.stop_on_failure_checkbox.setChecked(self.current_settings.stop_batch_on_first_failure)
        form.addRow("Batch Queue:", self.stop_on_failure_checkbox)

        for theme_name in get_available_themes():
            self.theme_combo.addItem(theme_name)
        self.theme_combo.addItem("system")
        current_theme = getattr(self.current_settings, "theme", DEFAULT_DARK_THEME)
        self.theme_combo.setCurrentText(current_theme)
        form.addRow("UI Theme:", self.theme_combo)
        self._add_usb_reserve_rows(form)

        root.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addWidget(self.cleanup_btn)
        buttons.addStretch()
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)

        self.save_btn.clicked.connect(self.save_settings)
        self.cancel_btn.clicked.connect(self.reject)
        self.cleanup_btn.clicked.connect(self.open_cleanup_dialog)

    def _add_usb_reserve_rows(self, form: QFormLayout) -> None:
        """Add the two USB export reserve inputs. The larger reserve kind applies."""
        self.usb_reserve_percent_spin.setDecimals(USB_RESERVE_PERCENT_DECIMALS)
        self.usb_reserve_percent_spin.setRange(0.0, MAX_USB_RESERVE_PERCENT)
        self.usb_reserve_percent_spin.setValue(self.current_settings.usb_reserve_percent)
        form.addRow("USB Export Reserve (% of export size):", self.usb_reserve_percent_spin)

        self.usb_reserve_mib_spin.setRange(0, MAX_USB_RESERVE_MIB)
        # A partial MiB rounds up. A save never shrinks the stored reserve.
        stored_mib = -(-self.current_settings.usb_reserve_bytes // BYTES_PER_MIB)
        self.usb_reserve_mib_spin.setValue(stored_mib)
        form.addRow("USB Export Reserve (MiB):", self.usb_reserve_mib_spin)

    def open_cleanup_dialog(self) -> None:
        """Open the cleanup dialog. A confirmed choice closes Settings without saving."""
        dialog = CleanupDialog(data_folders(self.storage_locations), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.cleanup_choice = dialog.choice()
        self.reject()

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
        self.current_settings.fake_lossless_method = self.fake_lossless_method_combo.currentText()
        self.current_settings.playlist_relative_paths = self.playlist_relative_checkbox.isChecked()
        self.current_settings.stop_batch_on_first_failure = (
            self.stop_on_failure_checkbox.isChecked()
        )
        self.current_settings.theme = self.theme_combo.currentText()
        self.current_settings.usb_reserve_percent = self.usb_reserve_percent_spin.value()
        self.current_settings.usb_reserve_bytes = self.usb_reserve_mib_spin.value() * BYTES_PER_MIB
        self.settings_mgr.save(self.current_settings)
        app = QApplication.instance()
        if app is not None:
            apply_application_theme(app, self.current_settings.theme)
        self.accept()
