"""Identification settings tab for provider credentials and the optional local LLM."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QWidget

from infiniteprep.adapters.catalog.itunes import DISCLOSURE_TEXT
from infiniteprep.adapters.llm.ollama_openai import RECOMMENDED_MODELS
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import AppSettings
from infiniteprep.ui.settings.base import FormSettingsTab


class IdentificationTab(FormSettingsTab):
    """Settings tab for AcoustID and Discogs credentials and LLM arbitration."""

    TITLE = "Identification"

    def __init__(
        self,
        settings: AppSettings,
        credential_store: CredentialStore,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the tab from the stored settings and the credential store."""
        super().__init__(parent)
        self.creds = credential_store
        # Credential store entry name for each masked input.
        self._masked_inputs: dict[str, QLineEdit] = {}
        self.acoustid_key_input = self._add_masked_row("AcoustID API Key:", "acoustid_api_key")
        self.discogs_token_input = self._add_masked_row("Discogs Personal Token:", "discogs_token")

        self.enable_llm_checkbox = QCheckBox("Enable Closed-World LLM Arbitration", self)
        self.enable_llm_checkbox.setChecked(settings.enable_llm_arbitration)
        self.form.addRow("Optional Local LLM:", self.enable_llm_checkbox)

        self.ollama_url_input = QLineEdit(settings.ollama_base_url, self)
        self.form.addRow("Ollama / OpenAI Base URL:", self.ollama_url_input)

        self.ollama_model_combo = QComboBox(self)
        self.ollama_model_combo.setEditable(True)
        for model in RECOMMENDED_MODELS:
            self.ollama_model_combo.addItem(model)
        self.ollama_model_combo.setCurrentText(settings.ollama_model)
        self.form.addRow("Ollama Model:", self.ollama_model_combo)

        # The disclosure names the host and the data that leaves the machine.
        self.disclosure_label = QLabel(DISCLOSURE_TEXT, self)
        self.disclosure_label.setWordWrap(True)
        self.form.addRow(self.disclosure_label)

    def _add_masked_row(self, label: str, entry_name: str) -> QLineEdit:
        """Add a masked input that shows the stored value of a store entry, and return it.

        entry_name names the credential store entry. The value itself never leaves
        the input and the store.
        """
        line_edit = QLineEdit(self.creds.get_secret(entry_name) or "", self)
        line_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.form.addRow(label, line_edit)
        self._masked_inputs[entry_name] = line_edit
        return line_edit

    def store_secrets(self) -> None:
        """Write each nonblank credential input to the credential store.

        A blank input keeps the stored secret.
        """
        for entry_name, line_edit in self._masked_inputs.items():
            value = line_edit.text().strip()
            if value:
                self.creds.set_secret(entry_name, value)

    def apply_to(self, settings: AppSettings) -> None:
        """Copy the LLM arbitration inputs into settings."""
        settings.enable_llm_arbitration = self.enable_llm_checkbox.isChecked()
        settings.ollama_base_url = self.ollama_url_input.text().strip()
        settings.ollama_model = self.ollama_model_combo.currentText().strip()


TAB_CLASS = IdentificationTab
