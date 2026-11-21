"""UI theme resolution and styling adapter supporting qt-material and native fallback."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Final

from infiniteprep.config.settings import (
    DARK_THEME_ID,
    LIGHT_THEME_ID,
    TEXT_SIZE_STANDARD,
    replace_legacy_theme,
)
from infiniteprep.ui.stylesheet import build_style_sheet
from infiniteprep.ui.tokens import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    Palette,
    StatusRole,
    text_sizes_for,
)

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QComboBox
except ImportError:
    Qt = None
    QGuiApplication = None
    QApplication = None
    QComboBox = None

logger = logging.getLogger(__name__)

DEFAULT_DARK_THEME: Final[str] = DARK_THEME_ID
DEFAULT_LIGHT_THEME: Final[str] = LIGHT_THEME_ID
# The native style is the fallback when qt-material cannot apply. A picker shows it
# only when no other theme can apply.
NATIVE_THEME: Final[str] = "native"
SYSTEM_THEME: Final[str] = "system"

AVAILABLE_THEMES: Final[tuple[str, ...]] = (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    NATIVE_THEME,
)
PICKER_THEMES: Final[tuple[str, ...]] = (DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME)
THEME_PALETTES: Final[dict[str, Palette]] = {
    DEFAULT_DARK_THEME: DARK_PALETTE,
    DEFAULT_LIGHT_THEME: LIGHT_PALETTE,
}
# The project theme files ship inside this package, next to this module.
PROJECT_THEMES_DIR: Final[Path] = Path(__file__).resolve().parent / "themes"
# qt-material sizes its text in pixels. The text size presets name points at 96 DPI.
PIXELS_PER_POINT: Final[float] = 96 / 72
# The pickers show these labels. Settings keep the identifiers, so stored files stay valid.
THEME_LABELS: Final[dict[str, str]] = {
    DEFAULT_DARK_THEME: "Dark",
    DEFAULT_LIGHT_THEME: "Light",
    NATIVE_THEME: "Native",
    SYSTEM_THEME: "Match System",
}


def theme_label(theme: str) -> str:
    """Return the picker label of a theme identifier, or the identifier when unknown."""
    return THEME_LABELS.get(theme, theme)


def fill_theme_picker(combo: QComboBox, themes: list[str]) -> None:
    """Add one readable label per theme. Each item keeps its identifier as item data."""
    for theme in themes:
        combo.addItem(theme_label(theme), theme)


def select_theme(combo: QComboBox, theme: str) -> bool:
    """Point a theme picker at the theme identifier. Return False when it lacks the theme.

    A legacy theme value selects the matching project theme.
    """
    index = combo.findData(theme)
    if index < 0:
        index = combo.findData(replace_legacy_theme(theme))
    if index < 0:
        return False
    combo.setCurrentIndex(index)
    return True


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
        module_spec = importlib.util.find_spec("qt_material")
    except (ImportError, ValueError):
        return None
    if module_spec is None or module_spec.origin is None:
        return None
    return Path(module_spec.origin).parent


def theme_file_path(theme: str) -> Path:
    """Return the path of a project theme file."""
    return PROJECT_THEMES_DIR / theme


def is_theme_applicable(theme: str) -> bool:
    """Return True when this build can apply the theme."""
    if theme == NATIVE_THEME:
        return True
    if theme == SYSTEM_THEME:
        return is_theme_applicable(resolve_theme(SYSTEM_THEME))
    if theme not in THEME_PALETTES or _qt_material_package_dir() is None:
        return False
    return theme_file_path(theme).is_file()


def get_applicable_themes() -> list[str]:
    """Return the themes that this build can apply."""
    return [theme for theme in AVAILABLE_THEMES if is_theme_applicable(theme)]


def get_theme_choices() -> list[str]:
    """Return every theme value the pickers offer, in display order.

    The pickers offer the native style only when no other theme can apply, so a
    picker never shows an empty list.
    """
    choices = [theme for theme in PICKER_THEMES if is_theme_applicable(theme)]
    if not choices:
        return [NATIVE_THEME]
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
    if theme_choice == NATIVE_THEME:
        return NATIVE_THEME
    replaced = replace_legacy_theme(theme_choice)
    if replaced in AVAILABLE_THEMES:
        return replaced
    return DEFAULT_DARK_THEME


def _clear_stylesheet(app: QApplication) -> None:
    """Remove any custom style sheet from the application."""
    if app is not None and hasattr(app, "setStyleSheet"):
        app.setStyleSheet("")


def _report_failure(theme_name: str, reason: str) -> None:
    """Log a warning that names the theme and the reason it did not apply."""
    logger.warning("The theme '%s' did not apply: %s", theme_name, reason)


def _qt_material_extra(palette: Palette, text_size: str) -> dict[str, str]:
    """Return the qt-material extra values for a palette and a text size preset."""
    body_pixels = round(text_sizes_for(text_size).body * PIXELS_PER_POINT)
    return {
        "font_size": f"{body_pixels}px",
        "danger": palette.status[StatusRole.FAILED],
        "warning": palette.status[StatusRole.WARNING],
        "success": palette.status[StatusRole.DONE],
    }


def _apply_project_theme(
    module: ModuleType, app: QApplication, resolved: str, text_size: str
) -> None:
    """Apply a project theme file through qt-material and add the app style sheet.

    The light theme file holds its line and surface colors in their final roles, so
    the qt-material invert_secondary swap stays off for both themes. qt-material
    returns without a style sheet when it cannot read the theme file. The app sheet
    then stays off too, so no partial theme sits over the native style.
    """
    palette = THEME_PALETTES[resolved]
    module.apply_stylesheet(
        app,
        theme=str(theme_file_path(resolved)),
        invert_secondary=False,
        extra=_qt_material_extra(palette, text_size),
    )
    material_sheet = app.styleSheet()
    if not material_sheet:
        return
    app.setStyleSheet(f"{material_sheet}\n{build_style_sheet(palette, text_size)}")


def apply_application_theme(
    app: QApplication, theme_name: str, text_size: str = TEXT_SIZE_STANDARD
) -> ThemeApplication:
    """Apply the chosen theme and report whether the application took effect.

    A theme that cannot apply leaves the native theme in effect. The result names
    the theme value in effect and the reason a theme did not apply. A legacy theme
    value applies the matching project theme.
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
        _apply_project_theme(module, app, resolved, text_size)
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
    in_effect = SYSTEM_THEME if theme_name == SYSTEM_THEME else resolved
    return ThemeApplication(applied=True, theme=in_effect)


def switch_application_theme(
    app: QApplication,
    theme_name: str,
    current_theme: str,
    text_size: str = TEXT_SIZE_STANDARD,
) -> ThemeApplication:
    """Apply a new theme and keep the current theme when the new one fails.

    The current theme stays in effect when it applies again. The native theme is
    in effect when the current theme fails too. The result names the theme value
    in effect, so the caller can show it and store it.
    """
    result = apply_application_theme(app, theme_name, text_size)
    if result.applied:
        return result
    fallback = current_theme or NATIVE_THEME
    restored = apply_application_theme(app, fallback, text_size)
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
