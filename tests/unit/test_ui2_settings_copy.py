"""Tests for the plain copy of the Settings dialog (#342 item 12)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QWidget

from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import SettingsManager
from infiniteprep.ui.settings import storage as storage_module
from infiniteprep.ui.settings.analysis import STOP_ON_FAILURE_LABEL
from infiniteprep.ui.settings.base import FormSettingsTab
from infiniteprep.ui.settings.storage import format_removed_count
from infiniteprep.ui.settings_dialog import SettingsDialog


def row_label(tab: FormSettingsTab, field: QWidget) -> str:
    """Return the form label text beside one field of a settings tab."""
    label = tab.form.labelForField(field)
    assert label is not None
    return label.text()


def open_dialog(qtbot: Any, tmp_path: Path) -> SettingsDialog:
    """Open the Settings dialog over an empty settings file."""
    dialog = SettingsDialog(
        SettingsManager(tmp_path / "settings.json"), CredentialStore(use_memory_backend=True)
    )
    qtbot.addWidget(dialog)
    return dialog


def test_window_and_buttons_are_plain(qtbot: Any, tmp_path: Path) -> None:
    """The dialog title and its save button use one plain word each."""
    dialog = open_dialog(qtbot, tmp_path)

    assert dialog.windowTitle() == "Settings"
    assert dialog.save_btn.text() == "Save"


def test_general_and_analysis_rows_are_plain(qtbot: Any, tmp_path: Path) -> None:
    """General and Analysis rows read in plain words."""
    dialog = open_dialog(qtbot, tmp_path)
    general = dialog.general_tab
    analysis = dialog.analysis_tab

    assert [
        row_label(general, general.theme_combo),
        row_label(general, general.unsorted_rows_placement_combo),
        row_label(general, general.text_size_combo),
        row_label(general, general.tag_key_notation_combo),
        row_label(general, general.tag_bpm_decimals_combo),
    ] == ["Theme:", "Empty values sort:", "Text size:", "Key format in tags:", "BPM in tags:"]
    assert general.tag_bpm_decimals_combo.itemText(0) == "Whole number"
    assert row_label(analysis, analysis.stop_on_failure_checkbox) == "Queue:"
    assert STOP_ON_FAILURE_LABEL == "Stop when a file fails"
    assert row_label(analysis, analysis.fake_lossless_method_combo) == "Fake lossless check:"


def test_identification_and_transcode_rows_are_plain(qtbot: Any, tmp_path: Path) -> None:
    """Identification and Transcode rows name what each item does."""
    dialog = open_dialog(qtbot, tmp_path)
    ident = dialog.identification_tab
    transcode = dialog.transcode_tab

    assert [
        row_label(ident, ident.acoustid_key_input),
        row_label(ident, ident.discogs_token_input),
        row_label(ident, ident.enable_llm_checkbox),
        row_label(ident, ident.ollama_url_input),
        row_label(ident, ident.ollama_model_combo),
    ] == ["AcoustID key:", "Discogs token:", "Local AI:", "AI server address:", "AI model:"]
    assert ident.enable_llm_checkbox.text() == "Let a local AI pick between matches"
    assert [
        row_label(transcode, transcode.transcode_mode_combo),
        row_label(transcode, transcode.transcode_preset_combo),
        row_label(transcode, transcode.playlist_relative_checkbox),
    ] == ["Mode:", "Format:", "Playlists:"]


def test_storage_rows_and_messages_are_plain(qtbot: Any, tmp_path: Path) -> None:
    """Storage rows, buttons, and messages use plain words."""
    dialog = open_dialog(qtbot, tmp_path)
    storage = dialog.storage_tab

    assert [
        row_label(storage, storage.usb_recovery_expiry_checkbox),
        row_label(storage, storage.usb_recovery_expiry_days_spin),
        row_label(storage, storage.usb_recovery_cleanup_btn),
        row_label(storage, storage.cleanup_btn),
        row_label(storage, storage.usb_reserve_percent_spin),
        row_label(storage, storage.usb_reserve_mib_spin),
    ] == [
        "Auto-delete:",
        "Delete after (days):",
        "USB recovery data:",
        "App data:",
        "Extra USB space (%):",
        "Extra USB space (MiB):",
    ]
    module = storage_module
    assert module.CLEANUP_BUTTON_LABEL == "Delete app data..."
    assert module.RECOVERY_CLEANUP_BUTTON_LABEL == "Clear USB recovery data..."
    assert module.RECOVERY_CLEANUP_TITLE == "Clear USB recovery data"
    assert module.RECOVERY_CLEANUP_QUESTION == (
        "Delete USB recovery data?\n\n"
        "You won't be able to resume or undo an interrupted USB export. "
        "This can't be undone."
    )
    assert module.RECOVERY_READ_ERROR_TITLE == "Couldn't clear USB recovery data"
    assert module.RECOVERY_READ_ERROR_TEXT == (
        "Couldn't read the USB recovery data. Nothing was deleted."
    )
    assert format_removed_count(1) == "Deleted 1 USB recovery entry."
    assert format_removed_count(3) == "Deleted 3 USB recovery entries."
