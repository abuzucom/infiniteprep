"""Behavioral tests for the visual system: tokens, theme files, and text sizes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from infiniteprep.config.settings import (
    DARK_THEME_ID,
    LIGHT_THEME_ID,
    SUPPORTED_TEXT_SIZES,
    TEXT_SIZE_LARGE,
    TEXT_SIZE_STANDARD,
    AppSettings,
    SettingsManager,
)
from infiniteprep.ui.contrast import (
    GRAPHIC_CONTRAST_MINIMUM,
    TEXT_CONTRAST_MINIMUM,
    contrast_ratio,
    relative_luminance,
)
from infiniteprep.ui.settings.general import GeneralTab
from infiniteprep.ui.stylesheet import build_style_sheet
from infiniteprep.ui.theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    NATIVE_THEME,
    SYSTEM_THEME,
    THEME_PALETTES,
    apply_application_theme,
    get_theme_choices,
    resolve_theme,
    theme_file_path,
)
from infiniteprep.ui.tokens import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    OPEN_KEY_COUNT,
    TEXT_SIZES,
    Palette,
    TextSizePreset,
    key_cell_colors,
    scaled_size,
    spacing_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PALETTES = {"dark": DARK_PALETTE, "light": LIGHT_PALETTE}
STANDARD_SPACING = (4, 8, 12, 16, 24)
# Rounding a darker HSL shade to 8-bit RGB moves its hue by a few degrees at most.
HUE_ROUNDING_TOLERANCE = 4
# One qt-material color entry, such as <color name="primaryColor">#38bdf8</color>.
THEME_COLOR_PATTERN = re.compile(r'<color name="([A-Za-z]+)">(#[0-9a-f]{6})</color>')


def text_pairs(palette: Palette) -> list[tuple[str, str, str]]:
    """Return every (name, foreground, background) pair that carries text."""
    pairs = [("on-primary on primary", palette.on_primary, palette.primary)]
    for background_name, background in (
        ("background", palette.background),
        ("surface", palette.surface),
    ):
        pairs.append((f"text on {background_name}", palette.text, background))
        pairs.append((f"muted on {background_name}", palette.text_muted, background))
        pairs.append((f"highlight on {background_name}", palette.highlight, background))
        for role, color in palette.status.items():
            pairs.append((f"{role} on {background_name}", color, background))
    return pairs


def graphic_pairs(palette: Palette) -> list[tuple[str, str, str]]:
    """Return every (name, graphic, background) pair that must stand out as a graphic."""
    pairs = []
    for background_name, background in (
        ("background", palette.background),
        ("surface", palette.surface),
    ):
        pairs.append((f"focus ring on {background_name}", palette.highlight, background))
        pairs.append((f"primary button on {background_name}", palette.primary, background))
        for role, color in palette.status.items():
            pairs.append((f"{role} icon on {background_name}", color, background))
    return pairs


@pytest.mark.parametrize("theme", sorted(PALETTES))
def test_text_contrast(theme: str) -> None:
    """Every text color reaches 4.5:1 on each background it sits on."""
    for name, foreground, background in text_pairs(PALETTES[theme]):
        ratio = contrast_ratio(QColor(foreground), QColor(background))
        assert ratio >= TEXT_CONTRAST_MINIMUM, f"{theme} {name}: {ratio:.2f}"


@pytest.mark.parametrize("theme", sorted(PALETTES))
def test_graphic_contrast(theme: str) -> None:
    """Focus rings, primary buttons, and status icons reach 3:1 on each background."""
    for name, graphic, background in graphic_pairs(PALETTES[theme]):
        ratio = contrast_ratio(QColor(graphic), QColor(background))
        assert ratio >= GRAPHIC_CONTRAST_MINIMUM, f"{theme} {name}: {ratio:.2f}"


@pytest.mark.parametrize("is_minor", [False, True])
def test_key_cell_contrast(is_minor: bool) -> None:
    """Each key cell carries black or white text at 4.5:1 or more."""
    for open_key_number in range(1, OPEN_KEY_COUNT + 1):
        colors = key_cell_colors(open_key_number, is_minor)
        ratio = contrast_ratio(QColor(colors.text), QColor(colors.fill))
        assert ratio >= TEXT_CONTRAST_MINIMUM, f"{open_key_number} minor={is_minor}"


def test_minor_key_shade() -> None:
    """A minor key cell is darker than its major key cell and keeps the hue."""
    for open_key_number in range(1, OPEN_KEY_COUNT + 1):
        major = QColor(key_cell_colors(open_key_number, is_minor=False).fill)
        minor = QColor(key_cell_colors(open_key_number, is_minor=True).fill)
        assert relative_luminance(minor) < relative_luminance(major)
        assert abs(minor.hslHue() - major.hslHue()) <= HUE_ROUNDING_TOLERANCE


def test_key_hues_distinct() -> None:
    """The twelve major key fills are twelve different colors."""
    fills = {key_cell_colors(number, is_minor=False).fill for number in range(1, 13)}
    assert len(fills) == OPEN_KEY_COUNT


@pytest.mark.parametrize("open_key_number", [0, OPEN_KEY_COUNT + 1])
def test_bad_key_number(open_key_number: int) -> None:
    """Open Key numbers outside 1 to 12 raise ValueError."""
    with pytest.raises(ValueError, match="1 to 12"):
        key_cell_colors(open_key_number, is_minor=False)


def read_theme_colors(theme: str) -> dict[str, str]:
    """Return the named colors of a project theme file."""
    text = theme_file_path(theme).read_text(encoding="utf-8")
    return dict(THEME_COLOR_PATTERN.findall(text))


@pytest.mark.parametrize("theme", [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])
def test_theme_files_match(theme: str) -> None:
    """The qt-material roles in each theme file hold the palette colors."""
    palette = THEME_PALETTES[theme]
    colors = read_theme_colors(theme)

    assert colors["secondaryColor"] == palette.background
    assert colors["secondaryDarkColor"] == palette.surface
    assert colors["secondaryLightColor"] == palette.line
    assert colors["secondaryTextColor"] == palette.text
    assert colors["primaryColor"] == palette.highlight


@pytest.mark.parametrize("theme", [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])
def test_accent_text_contrast(theme: str) -> None:
    """qt-material puts primaryTextColor on primaryColor, so the pair reaches 4.5:1."""
    colors = read_theme_colors(theme)
    ratio = contrast_ratio(QColor(colors["primaryTextColor"]), QColor(colors["primaryColor"]))
    assert ratio >= TEXT_CONTRAST_MINIMUM


def test_logo_colors() -> None:
    """The dark theme uses the logo navy background, royal blue fill, and sky blue accent."""
    assert DARK_PALETTE.background == "#070b1c"
    assert DARK_PALETTE.primary == "#2a5ce6"
    assert DARK_PALETTE.highlight == "#38bdf8"


def test_text_size_presets() -> None:
    """Each preset holds its point size for each text role."""
    expected = {
        TextSizePreset.COMPACT: (9, 10, 12, 14),
        TextSizePreset.STANDARD: (10, 11, 13, 16),
        TextSizePreset.LARGE: (11, 12, 14, 18),
    }
    for preset, sizes in expected.items():
        actual = TEXT_SIZES[preset]
        assert (actual.caption, actual.body, actual.section_heading, actual.dialog_title) == sizes
    assert {preset.value for preset in TextSizePreset} == set(SUPPORTED_TEXT_SIZES)


def test_spacing_scale() -> None:
    """Standard spacing is 4 to 24 px, and Large spacing grows with the body size."""
    assert spacing_for(TEXT_SIZE_STANDARD) == STANDARD_SPACING
    large = spacing_for(TEXT_SIZE_LARGE)
    assert all(big >= small for big, small in zip(large, STANDARD_SPACING, strict=True))
    assert large[-1] > STANDARD_SPACING[-1]
    assert scaled_size(32, "unknown preset") == 32


def test_style_sheet() -> None:
    """The style sheet carries the body size, the caption header, and the button heights."""
    style = build_style_sheet(DARK_PALETTE, TEXT_SIZE_STANDARD)

    assert "* { font-size: 11pt; }" in style
    assert "QHeaderView::section { font-size: 10pt; color: #9aa6c8; }" in style
    assert "min-height: 28px" in style
    assert "min-height: 32px" in style
    assert "border: 2px solid #38bdf8" in style
    assert "background-color: #2a5ce6" in style


def test_resolve_legacy_themes() -> None:
    """The teal themes resolve to the project themes, and the native fallback stays native."""
    assert resolve_theme("dark_teal.xml") == DEFAULT_DARK_THEME
    assert resolve_theme("light_teal.xml") == DEFAULT_LIGHT_THEME
    assert resolve_theme(NATIVE_THEME) == NATIVE_THEME


def test_pickers_skip_native(tmp_path: Path, monkeypatch: Any) -> None:
    """With qt-material present, the pickers offer Dark, Light, and Match System only."""
    monkeypatch.setattr("infiniteprep.ui.theme._qt_material_package_dir", lambda: tmp_path)

    assert get_theme_choices() == [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, SYSTEM_THEME]


def test_native_without_engine(monkeypatch: Any) -> None:
    """Without qt-material, no project theme applies, so the pickers offer native only."""
    monkeypatch.setattr("infiniteprep.ui.theme._qt_material_package_dir", lambda: None)

    assert get_theme_choices() == [NATIVE_THEME]


def apply_with_engine(theme: str, text_size: str) -> tuple[MagicMock, MagicMock, Any]:
    """Apply a theme through a recording engine. Return the app, engine, and result."""
    app = MagicMock(spec=QApplication)
    app.styleSheet.return_value = "/* qt-material */"
    engine = MagicMock()
    with (
        patch("infiniteprep.ui.theme._has_qt_material", return_value=True),
        patch("infiniteprep.ui.theme.is_theme_applicable", return_value=True),
        patch("infiniteprep.ui.theme._load_qt_material", return_value=engine),
    ):
        result = apply_application_theme(app, theme, text_size)
    return app, engine, result


def test_theme_applies_files() -> None:
    """qt-material receives the project file path, and the app sheet follows its sheet."""
    app, engine, result = apply_with_engine(DEFAULT_LIGHT_THEME, TEXT_SIZE_LARGE)

    assert result.applied is True
    assert result.theme == DEFAULT_LIGHT_THEME
    call = engine.apply_stylesheet.call_args
    assert Path(call.kwargs["theme"]) == theme_file_path(DEFAULT_LIGHT_THEME)
    assert Path(call.kwargs["theme"]).is_file()
    assert call.kwargs["invert_secondary"] is False
    assert call.kwargs["extra"]["font_size"] == "16px"
    applied_sheet = app.setStyleSheet.call_args.args[0]
    assert applied_sheet.startswith("/* qt-material */")
    assert build_style_sheet(LIGHT_PALETTE, TEXT_SIZE_LARGE) in applied_sheet


def test_legacy_theme_applies() -> None:
    """A stored teal theme applies and reports the matching project theme."""
    _app, engine, result = apply_with_engine("dark_teal.xml", TEXT_SIZE_STANDARD)

    assert result.theme == DEFAULT_DARK_THEME
    assert Path(engine.apply_stylesheet.call_args.kwargs["theme"]).name == DARK_THEME_ID


@pytest.mark.parametrize(
    ("stored", "loaded"),
    [
        ("dark_teal.xml", DARK_THEME_ID),
        ("light_teal.xml", LIGHT_THEME_ID),
        ("native", DARK_THEME_ID),
        ("system", "system"),
    ],
)
def test_settings_map_legacy_themes(tmp_path: Path, stored: str, loaded: str) -> None:
    """A settings file that names a legacy theme loads with the matching project theme."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": stored}), encoding="utf-8")

    assert SettingsManager(path).load().theme == loaded


def test_text_size_setting(tmp_path: Path) -> None:
    """A stored preset loads as stored. An unknown preset loads as Standard."""
    path = tmp_path / "settings.json"
    manager = SettingsManager(path)
    manager.save(AppSettings(text_size=TEXT_SIZE_LARGE))
    assert manager.load().text_size == TEXT_SIZE_LARGE

    path.write_text(json.dumps({"text_size": "huge"}), encoding="utf-8")
    assert manager.load().text_size == TEXT_SIZE_STANDARD
    assert AppSettings().text_size == TEXT_SIZE_STANDARD
    assert AppSettings().theme == DARK_THEME_ID


def test_general_tab_text_size(qtbot: Any) -> None:
    """The General tab shows the stored preset and copies the chosen preset into settings."""
    tab = GeneralTab(DEFAULT_DARK_THEME, settings=AppSettings(text_size=TEXT_SIZE_LARGE))
    qtbot.addWidget(tab)
    assert tab.text_size_combo.currentData() == TEXT_SIZE_LARGE

    tab.text_size_combo.setCurrentIndex(tab.text_size_combo.findData(TEXT_SIZE_STANDARD))
    settings = AppSettings(text_size=TEXT_SIZE_LARGE)
    tab.apply_to(settings)

    assert settings.text_size == TEXT_SIZE_STANDARD


def test_general_tab_legacy_theme(qtbot: Any) -> None:
    """A General tab opened with a teal theme value shows the matching project theme."""
    tab = GeneralTab("light_teal.xml")
    qtbot.addWidget(tab)

    assert tab.theme_combo.currentData() == DEFAULT_LIGHT_THEME


def test_bundles_ship_themes() -> None:
    """The spec collects both theme files, and every bundle allowlist names them."""
    spec_text = (REPO_ROOT / "infiniteprep.spec").read_text(encoding="utf-8")
    for theme in (DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME):
        assert f'THEMES_SOURCE / "{theme}"' in spec_text
        for operating_system in ("linux", "macos", "windows"):
            allowlist = REPO_ROOT / "packaging" / "bundle-allowlist" / f"{operating_system}.txt"
            entries = allowlist.read_text(encoding="utf-8").splitlines()
            assert f"_internal/infiniteprep/ui/themes/{theme}" in entries
