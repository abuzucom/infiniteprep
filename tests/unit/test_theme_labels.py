"""Behavioral tests for readable theme labels and the hidden Playback settings tab."""

from __future__ import annotations

from typing import Any

from infiniteprep.config.settings import AppSettings
from infiniteprep.ui.settings.general import GeneralTab
from infiniteprep.ui.settings.playback import PlaybackTab
from infiniteprep.ui.theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    NATIVE_THEME,
    THEME_LABELS,
    get_theme_choices,
    theme_label,
)


def test_known_themes_have_readable_labels() -> None:
    """Every built-in theme shows a label without a file extension or underscore."""
    for theme in (DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, NATIVE_THEME):
        label = theme_label(theme)
        assert label == THEME_LABELS[theme]
        assert ".xml" not in label
        assert "_" not in label


def test_unknown_theme_shows_as_stored() -> None:
    """A theme without a label shows its identifier, so no picker item is blank."""
    assert theme_label("custom_theme.xml") == "custom_theme.xml"


def test_picker_shows_labels_stores_identifiers(qtbot: Any) -> None:
    """The General tab picker shows labels, and a save stores the theme identifier."""
    tab = GeneralTab(DEFAULT_LIGHT_THEME)
    qtbot.addWidget(tab)
    shown = [tab.theme_combo.itemText(index) for index in range(tab.theme_combo.count())]
    stored = [tab.theme_combo.itemData(index) for index in range(tab.theme_combo.count())]
    settings = AppSettings()

    tab.apply_to(settings)

    assert shown == [theme_label(theme) for theme in get_theme_choices()]
    assert stored == get_theme_choices()
    assert tab.theme_combo.currentText() == theme_label(DEFAULT_LIGHT_THEME)
    assert settings.theme == DEFAULT_LIGHT_THEME


def test_playback_tab_absent_from_dialog() -> None:
    """The Playback tab holds no setting, so the dialog leaves it out."""
    assert PlaybackTab.SHOWN is False
    assert GeneralTab.SHOWN is True
