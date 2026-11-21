"""Design tokens for the UI-R visual system: colors, key colors, text sizes, and spacing.

The palettes carry the color roles that UI-R step 3 (#245) defined. The dark palette
uses the logo navy background, the logo royal blue for primary buttons, and the logo
sky blue for focus, selection, and progress. Status colors follow the Okabe-Ito set.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from PySide6.QtGui import QColor

from infiniteprep.config.settings import TEXT_SIZE_COMPACT, TEXT_SIZE_LARGE, TEXT_SIZE_STANDARD
from infiniteprep.ui.contrast import contrast_ratio


class StatusRole(StrEnum):
    """Status color roles. Each state also has its own icon, so color never stands alone."""

    DONE = "done"
    READY = "ready"
    WARNING = "warning"
    FAILED = "failed"
    WAITING = "waiting"


@dataclass(frozen=True)
class Palette:
    """One theme's color roles as hex strings."""

    background: str
    surface: str
    line: str
    text: str
    text_muted: str
    primary: str
    on_primary: str
    highlight: str
    status: dict[StatusRole, str]


DARK_PALETTE: Final[Palette] = Palette(
    background="#070b1c",
    surface="#0e1430",
    line="#232c52",
    text="#e8ecf8",
    text_muted="#9aa6c8",
    # The logo royal blue #2456e0, a step lighter, so the fill reaches 3:1 on the surface.
    primary="#2a5ce6",
    on_primary="#ffffff",
    highlight="#38bdf8",
    status={
        StatusRole.DONE: "#2fc79b",
        StatusRole.READY: "#56b4e9",
        StatusRole.WARNING: "#e69f00",
        StatusRole.FAILED: "#f0763a",
        StatusRole.WAITING: "#9aa6c8",
    },
)

LIGHT_PALETTE: Final[Palette] = Palette(
    background="#ffffff",
    surface="#f4f6fb",
    line="#d5dbea",
    text="#111831",
    text_muted="#56607a",
    primary="#1d4ed8",
    on_primary="#ffffff",
    # Darker than the specimen value #0284c7, so text in this color reaches 4.5:1.
    highlight="#0369a1",
    status={
        StatusRole.DONE: "#00785a",
        StatusRole.READY: "#0072b2",
        StatusRole.WARNING: "#8a5a00",
        StatusRole.FAILED: "#b34700",
        StatusRole.WAITING: "#56607a",
    },
)

# One hue per Open Key number, 1 through 12, in wheel order.
OPEN_KEY_BASE_COLORS: Final[tuple[str, ...]] = (
    "#c837c0",
    "#7155ae",
    "#4590fa",
    "#22c0f8",
    "#2ee4e0",
    "#2fd58e",
    "#43b836",
    "#86c400",
    "#ffd500",
    "#ff6a05",
    "#ff4f2b",
    "#ff2d48",
)
OPEN_KEY_COUNT: Final[int] = len(OPEN_KEY_BASE_COLORS)
# Minor keys keep the hue and take this share of the major lightness.
MINOR_KEY_LIGHTNESS_SHARE: Final[float] = 0.7
LIGHTNESS_MAX: Final[int] = 255
BLACK_TEXT: Final[str] = "#000000"
WHITE_TEXT: Final[str] = "#ffffff"


@dataclass(frozen=True)
class KeyCellColors:
    """The fill of one key cell and the text color that reads on it at 4.5:1 or more."""

    fill: str
    text: str


def _with_lightness(color: QColor, lightness: int) -> QColor:
    """Return the color with the same hue and saturation at another HSL lightness."""
    hue, saturation, _lightness, _alpha = color.getHsl()
    return QColor.fromHsl(hue, saturation, max(0, min(LIGHTNESS_MAX, lightness)))


def readable_text_color(fill: QColor) -> str:
    """Return black or white, whichever has the higher contrast on the fill.

    White reaches 4.5:1 up to a luminance of about 0.183, and black from about 0.175.
    The ranges overlap, so the better of the two reaches 4.5:1 on every opaque fill.
    The key hues therefore keep their lightness, and no shift is needed.
    """
    return max(
        (BLACK_TEXT, WHITE_TEXT), key=lambda candidate: contrast_ratio(QColor(candidate), fill)
    )


def key_cell_colors(open_key_number: int, is_minor: bool) -> KeyCellColors:
    """Return the cell fill and text color for an Open Key number from 1 to 12.

    Minor keys use a darker shade of the same hue. Raise ValueError outside 1 to 12.
    """
    if not 1 <= open_key_number <= OPEN_KEY_COUNT:
        raise ValueError(f"Open Key numbers run from 1 to {OPEN_KEY_COUNT}: {open_key_number}")
    fill = QColor(OPEN_KEY_BASE_COLORS[open_key_number - 1])
    if is_minor:
        fill = _with_lightness(fill, round(fill.getHsl()[2] * MINOR_KEY_LIGHTNESS_SHARE))
    return KeyCellColors(fill=fill.name(), text=readable_text_color(fill))


class TextSizePreset(StrEnum):
    """Text size presets that the General settings tab offers."""

    COMPACT = TEXT_SIZE_COMPACT
    STANDARD = TEXT_SIZE_STANDARD
    LARGE = TEXT_SIZE_LARGE


DEFAULT_TEXT_SIZE_PRESET: Final[TextSizePreset] = TextSizePreset.STANDARD


@dataclass(frozen=True)
class TextSizes:
    """Point sizes at 100 percent display scaling for each text role."""

    caption: int
    body: int
    section_heading: int
    dialog_title: int


TEXT_SIZES: Final[dict[TextSizePreset, TextSizes]] = {
    TextSizePreset.COMPACT: TextSizes(caption=9, body=10, section_heading=12, dialog_title=14),
    TextSizePreset.STANDARD: TextSizes(caption=10, body=11, section_heading=13, dialog_title=16),
    TextSizePreset.LARGE: TextSizes(caption=11, body=12, section_heading=14, dialog_title=18),
}

# Logical pixel sizes at the Standard preset. Other presets scale them by body size.
STANDARD_SPACING: Final[tuple[int, ...]] = (4, 8, 12, 16, 24)
STANDARD_PRIMARY_BUTTON_HEIGHT: Final[int] = 32
STANDARD_SECONDARY_BUTTON_HEIGHT: Final[int] = 28
STANDARD_ICON_BUTTON_SIZE: Final[int] = 28
STANDARD_TABLE_ROW_HEIGHT: Final[int] = 24
CORNER_RADIUS: Final[int] = 4
FOCUS_RING_WIDTH: Final[int] = 2


def text_sizes_for(preset: str) -> TextSizes:
    """Return the text sizes of a preset value. Unknown values get the Standard sizes."""
    try:
        return TEXT_SIZES[TextSizePreset(preset)]
    except ValueError:
        return TEXT_SIZES[DEFAULT_TEXT_SIZE_PRESET]


def scale_for(preset: str) -> float:
    """Return the size factor of a preset relative to Standard, from its body size."""
    standard_body = TEXT_SIZES[DEFAULT_TEXT_SIZE_PRESET].body
    return text_sizes_for(preset).body / standard_body


def scaled_size(standard_size: int, preset: str) -> int:
    """Return a Standard logical pixel size scaled for a preset."""
    return round(standard_size * scale_for(preset))


def spacing_for(preset: str) -> tuple[int, ...]:
    """Return the five spacing steps for a preset in logical pixels."""
    return tuple(scaled_size(step, preset) for step in STANDARD_SPACING)
