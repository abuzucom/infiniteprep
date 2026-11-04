"""UI theme resolution and styling adapter supporting qt-material and native fallback."""

from __future__ import annotations

import importlib.util
import logging
from typing import Final

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
except ImportError:
    Qt = None
    QGuiApplication = None
    QApplication = None

logger = logging.getLogger(__name__)

DEFAULT_DARK_THEME: Final[str] = "dark_teal.xml"
DEFAULT_LIGHT_THEME: Final[str] = "light_teal.xml"
NATIVE_THEME: Final[str] = "native"
SYSTEM_THEME: Final[str] = "system"

AVAILABLE_THEMES: Final[tuple[str, ...]] = (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    NATIVE_THEME,
)


def get_available_themes() -> list[str]:
    """Return list of supported theme identifiers."""
    return list(AVAILABLE_THEMES)


def _is_system_in_dark_mode() -> bool:
    """Return True if host operating system reports dark mode color scheme."""
    if QGuiApplication is None or Qt is None:
        return True
    app = QGuiApplication.instance()
    if app is None:
        return True
    hints = app.styleHints()
    if hints is None:
        return True
    return hints.colorScheme() != Qt.ColorScheme.Light


def _has_qt_material() -> bool:
    """Return True if qt_material library is installed and importable."""
    return importlib.util.find_spec("qt_material") is not None


def resolve_theme(theme_choice: str | None = None) -> str:
    """Resolve user theme selection or system preference to concrete theme name."""
    if not theme_choice or theme_choice == SYSTEM_THEME:
        return DEFAULT_DARK_THEME if _is_system_in_dark_mode() else DEFAULT_LIGHT_THEME
    if theme_choice in AVAILABLE_THEMES:
        return theme_choice
    return DEFAULT_DARK_THEME


def apply_application_theme(app: QApplication, theme_name: str) -> None:
    """Apply chosen theme to target QApplication with fallback to native styling."""
    resolved = resolve_theme(theme_name)
    if resolved == NATIVE_THEME or not _has_qt_material():
        if app is not None and hasattr(app, "setStyleSheet"):
            app.setStyleSheet("")
        return
    try:
        qt_material = importlib.import_module("qt_material")

        qt_material.apply_stylesheet(app, theme=resolved)
    except (RuntimeError, AttributeError, TypeError, ValueError, OSError) as exc:
        logger.warning("Failed to apply qt-material theme '%s': %s", resolved, exc)
        if app is not None and hasattr(app, "setStyleSheet"):
            app.setStyleSheet("")
