"""Unit tests for UI theme resolution, application, and settings persistence."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from unittest.mock import MagicMock, patch

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

from infiniteprep.config.settings import AppSettings, SettingsManager
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
