"""Unit tests for UI theme resolution, application, and settings persistence."""

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
    apply_application_theme,
    get_available_themes,
    resolve_theme,
)


def test_get_available_themes_contains_dark_and_light() -> None:
    """Verify available themes list includes dark and light theme options."""
    themes = get_available_themes()
    assert DEFAULT_DARK_THEME in themes
    assert DEFAULT_LIGHT_THEME in themes
    assert "native" in themes


def test_resolve_theme_explicit_choice() -> None:
    """Verify explicitly requested theme names pass through validation."""
    assert resolve_theme(DEFAULT_DARK_THEME) == DEFAULT_DARK_THEME
    assert resolve_theme(DEFAULT_LIGHT_THEME) == DEFAULT_LIGHT_THEME
    assert resolve_theme("native") == "native"
    assert resolve_theme("unknown_invalid_theme") == DEFAULT_DARK_THEME


def test_resolve_theme_system_detection() -> None:
    """Verify system color scheme detection routes to dark or light defaults."""
    with patch("infiniteprep.ui.theme._is_system_in_dark_mode", return_value=True):
        assert resolve_theme("system") == DEFAULT_DARK_THEME

    with patch("infiniteprep.ui.theme._is_system_in_dark_mode", return_value=False):
        assert resolve_theme("system") == DEFAULT_LIGHT_THEME


def test_apply_application_theme_native_clears_stylesheet() -> None:
    """Verify applying the native theme clears any custom stylesheet on QApplication."""
    app = MagicMock(spec=QApplication) if QApplication is not None else MagicMock()
    apply_application_theme(app, "native")
    app.setStyleSheet.assert_called_once_with("")


def test_apply_application_theme_qt_material_fallback() -> None:
    """Verify apply_application_theme falls back gracefully when qt-material is missing."""
    app = MagicMock(spec=QApplication) if QApplication is not None else MagicMock()
    with patch("infiniteprep.ui.theme._has_qt_material", return_value=False):
        apply_application_theme(app, DEFAULT_DARK_THEME)
    app.setStyleSheet.assert_called_once_with("")


def test_settings_theme_persistence_roundtrip(tmp_path: Path) -> None:
    """Verify theme setting is preserved and deserialized by SettingsManager."""
    settings_file = tmp_path / "settings.json"
    mgr = SettingsManager(settings_file)
    initial = mgr.load()
    assert hasattr(initial, "theme")

    modified = AppSettings(theme=DEFAULT_LIGHT_THEME)
    mgr.save(modified)
    reloaded = mgr.load()
    assert reloaded.theme == DEFAULT_LIGHT_THEME


@pytest.mark.parametrize("window_kind", ["main", "settings"])
def test_theme_selectors_use_shared_default_for_legacy_settings(
    window_kind: str, qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy settings select the shared default in both application windows."""
    settings_values = asdict(AppSettings())
    settings_values.pop("theme")
    manager = MagicMock(spec=SettingsManager)
    manager.load.return_value = SimpleNamespace(**settings_values)
    module_name = f"infiniteprep.ui.{window_kind}_window"
    if window_kind == "settings":
        module_name = "infiniteprep.ui.settings_dialog"
    monkeypatch.setattr(f"{module_name}.DEFAULT_DARK_THEME", DEFAULT_LIGHT_THEME, raising=False)
    if window_kind == "main":
        monkeypatch.setattr(f"{module_name}.SettingsManager", lambda: manager)
        repository = Repository(DatabaseManager(":memory:"))
        window = MainWindow(
            repository, ImportService(repository, MetadataTagAdapter()), ReviewService(repository)
        )
    else:
        credentials = MagicMock(spec=CredentialStore)
        credentials.get_secret.return_value = None
        window = SettingsDialog(manager, credentials)
    qtbot.addWidget(window)
    assert window.theme_combo.currentText() == DEFAULT_LIGHT_THEME
