"""UI theme resolution and styling adapter supporting qt-material and native fallback."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
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


@dataclass(frozen=True)
class ThemeApplication:
    """Outcome of one theme application.

    The theme field names the theme in effect. A failed application leaves the
    native theme in effect, so the caller can show the theme that applies.
    """

    applied: bool
    theme: str
    reason: str = ""


def get_available_themes() -> list[str]:
    """Return list of supported theme identifiers."""
    return list(AVAILABLE_THEMES)


def _qt_material_package_dir() -> Path | None:
    """Return the folder of the qt_material package, or None when it is missing.

    The packaged app keeps the package in the bundle folder, so the theme files
    sit next to the package in both a source tree and a packaged build.
    """
    try:
        spec = importlib.util.find_spec("qt_material")
    except (ImportError, ValueError):
        return None
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


def is_theme_applicable(theme: str) -> bool:
    """Return True when this build can apply the theme."""
    if theme == NATIVE_THEME:
        return True
    if theme == SYSTEM_THEME:
        return is_theme_applicable(resolve_theme(SYSTEM_THEME))
    package_dir = _qt_material_package_dir()
    if package_dir is None:
        return False
    return (package_dir / "themes" / theme).is_file()


def get_applicable_themes() -> list[str]:
    """Return the themes that this build can apply."""
    return [theme for theme in AVAILABLE_THEMES if is_theme_applicable(theme)]


def get_theme_choices() -> list[str]:
    """Return every theme value the pickers offer, in display order."""
    choices = get_applicable_themes()
    if is_theme_applicable(SYSTEM_THEME):
        choices.append(SYSTEM_THEME)
    return choices


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


def _load_qt_material() -> ModuleType | None:
    """Return the qt_material module, or None when the import fails.

    The theme package stays optional at run time, so a missing package leaves
    the app on the native theme instead of stopping it.
    """
    try:
        return importlib.import_module("qt_material")
    except ImportError as exc:
        logger.warning("The qt-material package could not be imported: %s", exc)
        return None


def resolve_theme(theme_choice: str | None = None) -> str:
    """Resolve user theme selection or system preference to concrete theme name."""
    if not theme_choice or theme_choice == SYSTEM_THEME:
        return DEFAULT_DARK_THEME if _is_system_in_dark_mode() else DEFAULT_LIGHT_THEME
    if theme_choice in AVAILABLE_THEMES:
        return theme_choice
    return DEFAULT_DARK_THEME


def _clear_stylesheet(app: QApplication) -> None:
    """Remove any custom style sheet from the application."""
    if app is not None and hasattr(app, "setStyleSheet"):
        app.setStyleSheet("")


def _report_failure(theme_name: str, reason: str) -> None:
    """Log a warning that names the theme and the reason it did not apply."""
    logger.warning("The theme '%s' did not apply: %s", theme_name, reason)


def apply_application_theme(app: QApplication, theme_name: str) -> ThemeApplication:
    """Apply the chosen theme and report whether the application took effect.

    A theme that cannot apply leaves the native theme in effect. The result names
    the theme value in effect and the reason a theme did not apply.
    """
    resolved = resolve_theme(theme_name)
    if resolved == NATIVE_THEME:
        _clear_stylesheet(app)
        return ThemeApplication(applied=True, theme=NATIVE_THEME)
    failure = _theme_failure_reason(resolved)
    if failure:
        _report_failure(theme_name, failure)
        _clear_stylesheet(app)
        return ThemeApplication(applied=False, theme=NATIVE_THEME, reason=failure)
    try:
        module = _load_qt_material()
        if module is None:
            failure = "the qt-material package cannot be imported"
            _report_failure(theme_name, failure)
            _clear_stylesheet(app)
            return ThemeApplication(applied=False, theme=NATIVE_THEME, reason=failure)
        module.apply_stylesheet(app, theme=resolved)
    except (
        AttributeError,
        ImportError,
        IndexError,
        KeyError,
        NameError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        failure = f"qt-material could not apply the theme: {exc}"
        _report_failure(theme_name, failure)
        _clear_stylesheet(app)
        return ThemeApplication(applied=False, theme=NATIVE_THEME, reason=failure)
    return ThemeApplication(applied=True, theme=theme_name or resolved)


def switch_application_theme(
    app: QApplication, theme_name: str, current_theme: str
) -> ThemeApplication:
    """Apply a new theme and keep the current theme when the new one fails.

    The current theme stays in effect when it applies again. The native theme is
    in effect when the current theme fails too. The result names the theme value
    in effect, so the caller can show it and store it.
    """
    result = apply_application_theme(app, theme_name)
    if result.applied:
        return result
    fallback = current_theme or NATIVE_THEME
    restored = apply_application_theme(app, fallback)
    if restored.applied:
        return ThemeApplication(applied=False, theme=fallback, reason=result.reason)
    return ThemeApplication(applied=False, theme=NATIVE_THEME, reason=result.reason)


def _theme_failure_reason(resolved: str) -> str:
    """Return why the theme cannot apply, or an empty string when it can."""
    if not _has_qt_material():
        return "the qt-material package is missing"
    if not is_theme_applicable(resolved):
        return f"the theme file for '{resolved}' is missing"
    return ""
