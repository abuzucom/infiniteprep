"""Tabbed Settings dialog for provider credentials, transcode, storage, and app options."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from infiniteprep import storage_paths
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import (
    UNSORTED_PLACEMENT_FIRST,
    AppSettings,
    SettingsManager,
)
from infiniteprep.data_cleanup import CleanupChoice, data_folders
from infiniteprep.storage_paths import StorageLocations
from infiniteprep.ui.cleanup_dialog import CleanupDialog
from infiniteprep.ui.settings.analysis import STOP_ON_FAILURE_LABEL, AnalysisTab
from infiniteprep.ui.settings.base import SettingsTab
from infiniteprep.ui.settings.general import GeneralTab
from infiniteprep.ui.settings.identification import IdentificationTab
from infiniteprep.ui.settings.playback import PlaybackTab
from infiniteprep.ui.settings.storage import (
    CLEANUP_BUTTON_LABEL,
    USB_RESERVE_PERCENT_DECIMALS,
    StorageTab,
)
from infiniteprep.ui.settings.transcode import TranscodeTab
from infiniteprep.ui.settings.updates import UpdatesTab
from infiniteprep.ui.theme import DEFAULT_DARK_THEME, apply_application_theme

__all__ = [
    "CLEANUP_BUTTON_LABEL",
    "SETTINGS_DIALOG_HEIGHT",
    "SETTINGS_DIALOG_WIDTH",
    "STOP_ON_FAILURE_LABEL",
    "USB_RESERVE_PERCENT_DECIMALS",
    "SettingsDialog",
]

SETTINGS_DIALOG_WIDTH: Final[int] = 620
SETTINGS_DIALOG_HEIGHT: Final[int] = 460


class SettingsDialog(QDialog):
    """Modal tabbed dialog for every user setting, one tab per area (D351).

    The widget attributes from the single-form dialog stay on the dialog as
    references to the widgets on each tab.
    """

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

        self.tabs = QTabWidget(self)
        self.setting_tabs: list[SettingsTab] = self._create_tabs(cleanup_enabled)
        for tab in self.setting_tabs:
            self.tabs.addTab(tab, tab.TITLE)
        self._expose_tab_widgets()

        self.save_btn = QPushButton("Save Settings", self)
        self.cancel_btn = QPushButton("Cancel", self)
        self._build_layout()

    def _create_tabs(self, cleanup_enabled: bool) -> list[SettingsTab]:
        """Create every tab in display order. A new area adds one entry here."""
        settings = self.current_settings
        # Settings from an older file can lack a theme. This module resolves the
        # fallback theme and passes the result to the General tab.
        theme = getattr(settings, "theme", DEFAULT_DARK_THEME)
        placement = getattr(settings, "unsorted_rows_placement", UNSORTED_PLACEMENT_FIRST)
        self.general_tab = GeneralTab(theme, unsorted_placement=placement)
        self.playback_tab = PlaybackTab()
        self.analysis_tab = AnalysisTab(settings)
        self.transcode_tab = TranscodeTab(settings)
        self.identification_tab = IdentificationTab(settings, self.creds)
        self.updates_tab = UpdatesTab()
        self.storage_tab = StorageTab(settings, cleanup_enabled)
        return [
            self.general_tab,
            self.playback_tab,
            self.analysis_tab,
            self.transcode_tab,
            self.identification_tab,
            self.updates_tab,
            self.storage_tab,
        ]

    def _expose_tab_widgets(self) -> None:
        """Keep the single-form widget attributes as references to the tab widgets."""
        self.theme_combo = self.general_tab.theme_combo
        self.unsorted_rows_placement_combo = self.general_tab.unsorted_rows_placement_combo
        self.stop_on_failure_checkbox = self.analysis_tab.stop_on_failure_checkbox
        self.fake_lossless_method_combo = self.analysis_tab.fake_lossless_method_combo
        self.transcode_mode_combo = self.transcode_tab.transcode_mode_combo
        self.transcode_preset_combo = self.transcode_tab.transcode_preset_combo
        self.playlist_relative_checkbox = self.transcode_tab.playlist_relative_checkbox
        self.acoustid_key_input = self.identification_tab.acoustid_key_input
        self.discogs_token_input = self.identification_tab.discogs_token_input
        self.enable_llm_checkbox = self.identification_tab.enable_llm_checkbox
        self.ollama_url_input = self.identification_tab.ollama_url_input
        self.ollama_model_combo = self.identification_tab.ollama_model_combo
        self.usb_reserve_percent_spin = self.storage_tab.usb_reserve_percent_spin
        self.usb_reserve_mib_spin = self.storage_tab.usb_reserve_mib_spin
        self.cleanup_btn = self.storage_tab.cleanup_btn

    def _build_layout(self) -> None:
        """Place the tabs above the Save and Cancel buttons and connect the actions."""
        root = QVBoxLayout(self)
        root.addWidget(self.tabs)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)

        self.save_btn.clicked.connect(self.save_settings)
        self.cancel_btn.clicked.connect(self.reject)
        self.cleanup_btn.clicked.connect(self.open_cleanup_dialog)

    def open_cleanup_dialog(self) -> None:
        """Open the cleanup dialog. A confirmed choice closes Settings without saving."""
        dialog = CleanupDialog(data_folders(self.storage_locations), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.cleanup_choice = dialog.choice()
        self.reject()

    def save_settings(self) -> None:
        """Persist updated settings to JSON and secrets to CredentialStore."""
        self.identification_tab.store_secrets()
        for tab in self.setting_tabs:
            tab.apply_to(self.current_settings)
        self.settings_mgr.save(self.current_settings)
        app = QApplication.instance()
        if app is not None:
            apply_application_theme(app, self.current_settings.theme)
        self.accept()
