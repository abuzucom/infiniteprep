"""Behavioral tests for the theme result, the theme picker, and the failure path."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import SettingsManager
from infiniteprep.ui.settings_dialog import SettingsDialog
from infiniteprep.ui.theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    NATIVE_THEME,
    SYSTEM_THEME,
    apply_application_theme,
    get_theme_choices,
    switch_application_theme,
    theme_file_path,
)


@pytest.fixture
def theme_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a theme package folder that holds both theme files."""
    package_dir = tmp_path / "qt_material"
    (package_dir / "themes").mkdir(parents=True)
    for theme_file in (DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME):
        (package_dir / "themes" / theme_file).write_text("<resources/>", encoding="utf-8")
    monkeypatch.setattr("infiniteprep.ui.theme._qt_material_package_dir", lambda: package_dir)
    return package_dir


def test_apply_application_theme_reports_a_failure_with_a_reason() -> None:
    """A missing theme package leaves the native theme in effect and names the reason."""
    app = MagicMock(spec=QApplication)
    with patch("infiniteprep.ui.theme._has_qt_material", return_value=False):
        result = apply_application_theme(app, DEFAULT_DARK_THEME)

    assert result.applied is False
    assert result.theme == NATIVE_THEME
    assert "qt-material" in result.reason
    app.setStyleSheet.assert_called_once_with("")


def test_apply_application_theme_reports_the_applied_theme() -> None:
    """A theme that the engine accepts reports the theme now in effect."""
    app = MagicMock(spec=QApplication)
    engine = MagicMock()
    with (
        patch("infiniteprep.ui.theme._has_qt_material", return_value=True),
        patch("infiniteprep.ui.theme.is_theme_applicable", return_value=True),
        patch("infiniteprep.ui.theme._load_qt_material", return_value=engine),
    ):
        result = apply_application_theme(app, DEFAULT_DARK_THEME)

    assert result.applied is True
    assert result.theme == DEFAULT_DARK_THEME
    call = engine.apply_stylesheet.call_args
    assert engine.apply_stylesheet.call_count == 1
    assert call.args == (app,)
    assert Path(call.kwargs["theme"]) == theme_file_path(DEFAULT_DARK_THEME)


def test_apply_application_theme_reports_a_failure_when_the_engine_raises() -> None:
    """A theme engine error leaves the native theme in effect and names the cause."""
    app = MagicMock(spec=QApplication)
    engine = MagicMock()
    engine.apply_stylesheet.side_effect = OSError("the icon sources are missing")
    with (
        patch("infiniteprep.ui.theme._has_qt_material", return_value=True),
        patch("infiniteprep.ui.theme.is_theme_applicable", return_value=True),
        patch("infiniteprep.ui.theme._load_qt_material", return_value=engine),
    ):
        result = apply_application_theme(app, DEFAULT_DARK_THEME)

    assert result.applied is False
    assert result.theme == NATIVE_THEME
    assert "the icon sources are missing" in result.reason
    app.setStyleSheet.assert_called_once_with("")


def test_switch_application_theme_keeps_the_current_theme_when_the_new_one_fails() -> None:
    """A failed switch reapplies the current theme, so the app keeps its look."""
    app = MagicMock(spec=QApplication)
    engine = MagicMock()
    engine.apply_stylesheet.side_effect = [OSError("the icon sources are missing"), None]
    with (
        patch("infiniteprep.ui.theme._has_qt_material", return_value=True),
        patch("infiniteprep.ui.theme.is_theme_applicable", return_value=True),
        patch("infiniteprep.ui.theme._load_qt_material", return_value=engine),
    ):
        result = switch_application_theme(app, DEFAULT_LIGHT_THEME, DEFAULT_DARK_THEME)

    assert result.applied is False
    assert result.theme == DEFAULT_DARK_THEME
    assert "the icon sources are missing" in result.reason
    assert engine.apply_stylesheet.call_count == 2


def test_switch_application_theme_reports_the_native_theme_when_both_fail() -> None:
    """A failed switch and a failed restore leave the native theme in effect."""
    app = MagicMock(spec=QApplication)
    engine = MagicMock()
    engine.apply_stylesheet.side_effect = OSError("the icon sources are missing")
    with (
        patch("infiniteprep.ui.theme._has_qt_material", return_value=True),
        patch("infiniteprep.ui.theme.is_theme_applicable", return_value=True),
        patch("infiniteprep.ui.theme._load_qt_material", return_value=engine),
    ):
        result = switch_application_theme(app, DEFAULT_LIGHT_THEME, DEFAULT_DARK_THEME)

    assert result.applied is False
    assert result.theme == NATIVE_THEME


def test_theme_choices_list_only_the_themes_the_build_can_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build without the theme package offers the native theme only."""
    monkeypatch.setattr("infiniteprep.ui.theme._qt_material_package_dir", lambda: None)

    assert get_theme_choices() == [NATIVE_THEME]


def test_theme_choices_include_the_system_theme_when_a_theme_file_exists(
    theme_package: Path,
) -> None:
    """A build with the theme files offers them and the system theme. Native stays out."""
    assert get_theme_choices() == [
        DEFAULT_DARK_THEME,
        DEFAULT_LIGHT_THEME,
        SYSTEM_THEME,
    ]


def test_settings_dialog_keeps_the_stored_theme_when_it_cannot_apply(
    qtbot: Any, tmp_path: Path, theme_package: Path
) -> None:
    """A theme that cannot apply stays out of the settings file."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(dialog)
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData(DEFAULT_LIGHT_THEME))

    engine = MagicMock()
    engine.apply_stylesheet.side_effect = [OSError("the icon sources are missing"), None]
    with (
        patch("infiniteprep.ui.theme._has_qt_material", return_value=True),
        patch("infiniteprep.ui.theme.is_theme_applicable", return_value=True),
        patch("infiniteprep.ui.theme._load_qt_material", return_value=engine),
    ):
        dialog.save_settings()

    assert manager.load().theme == DEFAULT_DARK_THEME
    assert dialog.theme_combo.currentData() == DEFAULT_DARK_THEME
