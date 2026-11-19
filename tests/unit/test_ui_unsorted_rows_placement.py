"""Tests for the unsorted row placement setting (issue 248)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QWidget

from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import (
    SUPPORTED_UNSORTED_PLACEMENTS,
    UNSORTED_PLACEMENT_FIRST,
    UNSORTED_PLACEMENT_LAST,
    AppSettings,
    SettingsManager,
)
from infiniteprep.ui.settings.general import GeneralTab
from infiniteprep.ui.settings_dialog import SettingsDialog


def test_default_placement_is_first() -> None:
    """A new installation places the unsorted rows first."""
    assert AppSettings().unsorted_rows_placement == UNSORTED_PLACEMENT_FIRST


def test_supported_placements_hold_two_values() -> None:
    """The setting accepts the first and the last placement."""
    assert SUPPORTED_UNSORTED_PLACEMENTS == (UNSORTED_PLACEMENT_FIRST, UNSORTED_PLACEMENT_LAST)


def test_update_stores_the_placement(tmp_path: Path) -> None:
    """A saved placement survives a reload."""
    manager = SettingsManager(tmp_path / "settings.json")

    manager.update(unsorted_rows_placement=UNSORTED_PLACEMENT_LAST)

    assert manager.load().unsorted_rows_placement == UNSORTED_PLACEMENT_LAST


def test_update_refuses_an_unknown_placement(tmp_path: Path) -> None:
    """An unknown placement raises and writes nothing."""
    manager = SettingsManager(tmp_path / "settings.json")

    with pytest.raises(ValueError, match="unsorted_rows_placement"):
        manager.update(unsorted_rows_placement="middle")

    assert manager.load().unsorted_rows_placement == UNSORTED_PLACEMENT_FIRST


def test_a_stored_unknown_placement_falls_back_to_the_default(tmp_path: Path) -> None:
    """A settings file with an unknown placement loads with the default value."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"unsorted_rows_placement": "middle"}), encoding="utf-8")

    settings = SettingsManager(path).load()

    assert settings.unsorted_rows_placement == UNSORTED_PLACEMENT_FIRST


def test_general_tab_shows_the_stored_placement(qtbot: Any) -> None:
    """The General tab shows the stored placement when it opens."""
    tab = GeneralTab("dark_teal.xml", unsorted_placement=UNSORTED_PLACEMENT_LAST)
    qtbot.addWidget(tab)

    assert tab.unsorted_rows_placement_combo.currentData() == UNSORTED_PLACEMENT_LAST


def test_general_tab_writes_the_chosen_placement(qtbot: Any) -> None:
    """The General tab copies the chosen placement into the settings."""
    tab = GeneralTab("dark_teal.xml")
    qtbot.addWidget(tab)
    settings = AppSettings()

    combo = tab.unsorted_rows_placement_combo
    combo.setCurrentIndex(combo.findData(UNSORTED_PLACEMENT_LAST))
    tab.apply_to(settings)

    assert settings.unsorted_rows_placement == UNSORTED_PLACEMENT_LAST


def test_the_placement_combo_sits_on_the_general_tab(qtbot: Any, tmp_path: Path) -> None:
    """The settings dialog shows the placement combo on the General tab page."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(dialog)

    general_page = dialog.tabs.widget(0)

    assert isinstance(general_page, QWidget)
    assert general_page.isAncestorOf(dialog.unsorted_rows_placement_combo)


def test_saving_the_dialog_stores_the_placement(qtbot: Any, tmp_path: Path) -> None:
    """A save from the settings dialog persists the chosen placement."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(dialog)

    dialog.unsorted_rows_placement_combo.setCurrentIndex(
        dialog.unsorted_rows_placement_combo.findData(UNSORTED_PLACEMENT_LAST)
    )
    dialog.save_settings()

    assert manager.load().unsorted_rows_placement == UNSORTED_PLACEMENT_LAST
