"""Behavioral tests for the theme result, the theme picker, and the failure path."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.settings_dialog import SettingsDialog
from infiniteprep.ui.theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    NATIVE_THEME,
    SYSTEM_THEME,
    ThemeApplication,
    apply_application_theme,
    get_theme_choices,
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
    engine.apply_stylesheet.assert_called_once_with(app, theme=DEFAULT_DARK_THEME)


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


def test_theme_choices_list_only_the_themes_the_build_can_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build without theme files offers the native theme only."""
    package_dir = tmp_path / "qt_material"
    (package_dir / "themes").mkdir(parents=True)
    monkeypatch.setattr("infiniteprep.ui.theme._qt_material_package_dir", lambda: package_dir)

    assert get_theme_choices() == [NATIVE_THEME]


def test_theme_choices_include_the_system_theme_when_a_theme_file_exists(
    theme_package: Path,
) -> None:
    """A build with the theme files offers them and the system theme."""
    assert get_theme_choices() == [
        DEFAULT_DARK_THEME,
        DEFAULT_LIGHT_THEME,
        NATIVE_THEME,
        SYSTEM_THEME,
    ]


def build_main_window(manager: MagicMock, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    """Return a main window that reads its settings from the given manager."""
    monkeypatch.setattr("infiniteprep.ui.main_window.SettingsManager", lambda: manager)
    repository = Repository(DatabaseManager(":memory:"))
    return MainWindow(
        repository, ImportService(repository, MetadataTagAdapter()), ReviewService(repository)
    )


def make_settings_manager(theme: str) -> MagicMock:
    """Return a settings manager that reports the given theme."""
    manager = MagicMock(spec=SettingsManager)
    settings_values = asdict(AppSettings())
    settings_values["theme"] = theme
    manager.load.return_value = SimpleNamespace(**settings_values)
    return manager


def test_main_window_keeps_the_picker_on_the_theme_in_effect(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch, theme_package: Path
) -> None:
    """A theme that cannot apply leaves the picker on the theme in effect."""
    manager = make_settings_manager(DEFAULT_LIGHT_THEME)
    window = build_main_window(manager, monkeypatch)
    qtbot.addWidget(window)

    failed = ThemeApplication(applied=False, theme=NATIVE_THEME, reason="the theme file is missing")
    with patch("infiniteprep.ui.main_window.apply_application_theme", return_value=failed):
        window.theme_combo.setCurrentText(DEFAULT_DARK_THEME)

    assert window.theme_combo.currentText() == DEFAULT_LIGHT_THEME
    manager.save.assert_not_called()


def test_main_window_stores_a_theme_that_applied(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch, theme_package: Path
) -> None:
    """A theme that applies becomes the stored theme."""
    manager = make_settings_manager(DEFAULT_LIGHT_THEME)
    window = build_main_window(manager, monkeypatch)
    qtbot.addWidget(window)

    applied = ThemeApplication(applied=True, theme=DEFAULT_DARK_THEME)
    with patch("infiniteprep.ui.main_window.apply_application_theme", return_value=applied):
        window.theme_combo.setCurrentText(DEFAULT_DARK_THEME)

    assert window.theme_combo.currentText() == DEFAULT_DARK_THEME
    manager.save.assert_called_once()
    assert manager.save.call_args.args[0].theme == DEFAULT_DARK_THEME


def test_settings_dialog_keeps_the_stored_theme_when_it_cannot_apply(
    qtbot: Any, tmp_path: Path, theme_package: Path
) -> None:
    """A theme that cannot apply stays out of the settings file."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(dialog)
    dialog.theme_combo.setCurrentText(DEFAULT_LIGHT_THEME)

    failed = ThemeApplication(applied=False, theme=NATIVE_THEME, reason="the theme file is missing")
    with patch("infiniteprep.ui.settings_dialog.apply_application_theme", return_value=failed):
        dialog.save_settings()

    assert manager.load().theme == DEFAULT_DARK_THEME
    assert dialog.theme_combo.currentText() == DEFAULT_DARK_THEME
