"""Behavioral tests for the tabbed Settings dialog (INFRA-10, D351)."""

from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from infiniteprep.adapters.transcoder.safe_transcoder import TranscodeMode, TranscodePreset
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import BYTES_PER_MIB, AppSettings, SettingsManager
from infiniteprep.ui.settings_dialog import SettingsDialog
from infiniteprep.ui.theme import DEFAULT_LIGHT_THEME

EXPECTED_TAB_TITLES: Final[tuple[str, ...]] = (
    "General",
    "Analysis",
    "Transcode",
    "Identification",
    "Updates",
    "Storage",
)
TAB_MODULE_NAMES: Final[tuple[str, ...]] = (
    "general",
    "playback",
    "analysis",
    "transcode",
    "identification",
    "updates",
    "storage",
)
# Each existing widget attribute on SettingsDialog, keyed by the tab that holds it.
WIDGETS_BY_TAB: Final[dict[str, tuple[str, ...]]] = {
    "General": ("theme_combo",),
    "Analysis": ("stop_on_failure_checkbox",),
    "Transcode": (
        "transcode_mode_combo",
        "transcode_preset_combo",
        "playlist_relative_checkbox",
    ),
    "Identification": (
        "acoustid_key_input",
        "discogs_token_input",
        "enable_llm_checkbox",
        "ollama_url_input",
        "ollama_model_combo",
    ),
    "Storage": ("usb_reserve_percent_spin", "usb_reserve_mib_spin", "cleanup_btn"),
}
PLACEHOLDER_TABS: Final[tuple[str, ...]] = ("Updates",)
# A tab module whose tab holds no setting and stays out of the dialog.
HIDDEN_TAB_TITLES: Final[tuple[str, ...]] = ("Playback",)
STORED_RESERVE_MIB: Final[int] = 64
STORED_RESERVE_PERCENT: Final[float] = 12.5
ACOUSTID_KEY: Final[str] = "acoustid-test-key"
DISCOGS_CREDENTIAL: Final[str] = "discogs-test-token"


def non_default_settings() -> AppSettings:
    """Return settings where every field the dialog shows differs from its default."""
    return replace(
        AppSettings(),
        theme=DEFAULT_LIGHT_THEME,
        stop_batch_on_first_failure=True,
        transcode_mode=TranscodeMode.IN_PLACE_BACKED_UP.value,
        transcode_preset=TranscodePreset.XDJ_LOSSLESS_AUTO.value,
        playlist_relative_paths=False,
        enable_llm_arbitration=True,
        ollama_base_url="http://localhost:11434/v1",
        ollama_model="custom-model:1b",
        usb_reserve_percent=STORED_RESERVE_PERCENT,
        usb_reserve_bytes=STORED_RESERVE_MIB * BYTES_PER_MIB,
    )


def open_dialog(qtbot: Any, manager: SettingsManager, creds: CredentialStore) -> SettingsDialog:
    """Open a Settings dialog under qtbot cleanup."""
    dialog = SettingsDialog(manager, creds)
    qtbot.addWidget(dialog)
    return dialog


def tab_page(dialog: SettingsDialog, title: str) -> QWidget:
    """Return the page widget of the tab with the given title."""
    for index in range(dialog.tabs.count()):
        if dialog.tabs.tabText(index) == title:
            return dialog.tabs.widget(index)
    raise AssertionError(f"No Settings tab titled {title!r}.")


def test_dialog_shows_one_tab_per_area_in_order(qtbot: Any, tmp_path: Path) -> None:
    """The dialog shows each area with settings as a tab in a fixed order."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = open_dialog(qtbot, manager, CredentialStore(use_memory_backend=True))

    titles = tuple(dialog.tabs.tabText(index) for index in range(dialog.tabs.count()))

    assert titles == EXPECTED_TAB_TITLES
    assert not set(HIDDEN_TAB_TITLES) & set(titles)


@pytest.mark.parametrize("module_name", TAB_MODULE_NAMES)
def test_each_area_lives_in_its_own_module(module_name: str) -> None:
    """Each tab comes from its own module in the ui.settings package."""
    module = importlib.import_module(f"infiniteprep.ui.settings.{module_name}")

    assert issubclass(module.TAB_CLASS, QWidget)
    assert module.TAB_CLASS.TITLE in EXPECTED_TAB_TITLES + HIDDEN_TAB_TITLES


@pytest.mark.parametrize(("title", "attribute_names"), sorted(WIDGETS_BY_TAB.items()))
def test_every_existing_setting_sits_in_its_tab(
    qtbot: Any, tmp_path: Path, title: str, attribute_names: tuple[str, ...]
) -> None:
    """Opening a tab finds each of its existing settings on that tab's page."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = open_dialog(qtbot, manager, CredentialStore(use_memory_backend=True))
    page = tab_page(dialog, title)

    dialog.tabs.setCurrentWidget(page)

    assert dialog.tabs.currentWidget() is page
    for attribute_name in attribute_names:
        widget = getattr(dialog, attribute_name)
        assert page.isAncestorOf(widget), f"{attribute_name} is not on the {title} tab."


@pytest.mark.parametrize("title", PLACEHOLDER_TABS)
def test_areas_without_settings_show_a_note(qtbot: Any, tmp_path: Path, title: str) -> None:
    """Updates shows a one-line note until a setting joins the tab."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = open_dialog(qtbot, manager, CredentialStore(use_memory_backend=True))

    labels = tab_page(dialog, title).findChildren(QLabel)

    assert [label.text() for label in labels] == [f"No {title.lower()} settings yet."]


def test_stored_settings_load_into_the_tabs(qtbot: Any, tmp_path: Path) -> None:
    """Each tab shows the stored non-default value of each of its settings."""
    manager = SettingsManager(tmp_path / "settings.json")
    stored = non_default_settings()
    manager.save(stored)
    creds = CredentialStore(use_memory_backend=True)
    creds.set_secret("acoustid_api_key", ACOUSTID_KEY)
    creds.set_secret("discogs_token", DISCOGS_CREDENTIAL)

    dialog = open_dialog(qtbot, manager, creds)

    assert dialog.theme_combo.currentData() == stored.theme
    assert dialog.stop_on_failure_checkbox.isChecked() is True
    assert dialog.transcode_mode_combo.currentText() == stored.transcode_mode
    assert dialog.transcode_preset_combo.currentText() == stored.transcode_preset
    assert dialog.playlist_relative_checkbox.isChecked() is False
    assert dialog.acoustid_key_input.text() == ACOUSTID_KEY
    assert dialog.discogs_token_input.text() == DISCOGS_CREDENTIAL
    assert dialog.enable_llm_checkbox.isChecked() is True
    assert dialog.ollama_url_input.text() == stored.ollama_base_url
    assert dialog.ollama_model_combo.currentText() == stored.ollama_model
    assert dialog.usb_reserve_percent_spin.value() == STORED_RESERVE_PERCENT
    assert dialog.usb_reserve_mib_spin.value() == STORED_RESERVE_MIB


def test_saving_without_changes_keeps_every_setting(qtbot: Any, tmp_path: Path) -> None:
    """A save from the tabbed dialog writes back every stored setting unchanged."""
    manager = SettingsManager(tmp_path / "settings.json")
    stored = non_default_settings()
    manager.save(stored)
    creds = CredentialStore(use_memory_backend=True)
    creds.set_secret("acoustid_api_key", ACOUSTID_KEY)
    creds.set_secret("discogs_token", DISCOGS_CREDENTIAL)
    dialog = open_dialog(qtbot, manager, creds)

    dialog.save_settings()

    assert manager.load() == stored
    assert creds.get_secret("acoustid_api_key") == ACOUSTID_KEY
    assert creds.get_secret("discogs_token") == DISCOGS_CREDENTIAL


def test_edits_on_each_tab_save_and_reload(qtbot: Any, tmp_path: Path) -> None:
    """Values edited on every tab persist and show again in a reopened dialog."""
    manager = SettingsManager(tmp_path / "settings.json")
    creds = CredentialStore(use_memory_backend=True)
    expected = non_default_settings()
    dialog = open_dialog(qtbot, manager, creds)

    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData(expected.theme))
    dialog.stop_on_failure_checkbox.setChecked(True)
    dialog.transcode_mode_combo.setCurrentText(expected.transcode_mode)
    dialog.transcode_preset_combo.setCurrentText(expected.transcode_preset)
    dialog.playlist_relative_checkbox.setChecked(False)
    dialog.acoustid_key_input.setText(ACOUSTID_KEY)
    dialog.discogs_token_input.setText(DISCOGS_CREDENTIAL)
    dialog.enable_llm_checkbox.setChecked(True)
    dialog.ollama_url_input.setText(expected.ollama_base_url)
    dialog.ollama_model_combo.setCurrentText(expected.ollama_model)
    dialog.usb_reserve_percent_spin.setValue(STORED_RESERVE_PERCENT)
    dialog.usb_reserve_mib_spin.setValue(STORED_RESERVE_MIB)
    dialog.save_settings()

    assert manager.load() == expected
    reopened = open_dialog(qtbot, manager, creds)
    assert reopened.acoustid_key_input.text() == ACOUSTID_KEY
    assert reopened.discogs_token_input.text() == DISCOGS_CREDENTIAL
    assert reopened.ollama_model_combo.currentText() == expected.ollama_model
    assert reopened.usb_reserve_mib_spin.value() == STORED_RESERVE_MIB
