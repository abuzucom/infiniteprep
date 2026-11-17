"""WCAG contrast math and the theme-aware warning text color."""

from __future__ import annotations

from typing import Final

from PySide6.QtGui import QColor

# WCAG 2.x relative luminance constants for sRGB channels.
SRGB_LINEAR_LIMIT: Final[float] = 0.04045
SRGB_LINEAR_DIVISOR: Final[float] = 12.92
SRGB_GAMMA_OFFSET: Final[float] = 0.055
SRGB_GAMMA_DIVISOR: Final[float] = 1.055
SRGB_GAMMA_EXPONENT: Final[float] = 2.4
RED_WEIGHT: Final[float] = 0.2126
GREEN_WEIGHT: Final[float] = 0.7152
BLUE_WEIGHT: Final[float] = 0.0722
# The flare term in the WCAG contrast formula. It also keeps the divisor above zero.
CONTRAST_FLARE: Final[float] = 0.05
CHANNEL_MAX: Final[float] = 255.0
# WCAG AA minimums: 4.5:1 for normal text, 3:1 for graphics.
TEXT_CONTRAST_MINIMUM: Final[float] = 4.5
GRAPHIC_CONTRAST_MINIMUM: Final[float] = 3.0
# Amber 800 reads on light backgrounds, amber 400 on dark ones.
WARNING_ON_LIGHT: Final[str] = "#92400e"
WARNING_ON_DARK: Final[str] = "#fbbf24"


def _linear_channel(value: int) -> float:
    """Return one sRGB channel in linear light."""
    scaled = value / CHANNEL_MAX
    if scaled <= SRGB_LINEAR_LIMIT:
        return scaled / SRGB_LINEAR_DIVISOR
    return ((scaled + SRGB_GAMMA_OFFSET) / SRGB_GAMMA_DIVISOR) ** SRGB_GAMMA_EXPONENT


def relative_luminance(color: QColor) -> float:
    """Return the WCAG relative luminance of an opaque color."""
    return (
        RED_WEIGHT * _linear_channel(color.red())
        + GREEN_WEIGHT * _linear_channel(color.green())
        + BLUE_WEIGHT * _linear_channel(color.blue())
    )


def contrast_ratio(first: QColor, second: QColor) -> float:
    """Return the WCAG contrast ratio of two opaque colors, from 1 to 21."""
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + CONTRAST_FLARE) / (darker + CONTRAST_FLARE)


def blend_over(color: QColor, background: QColor) -> QColor:
    """Return the opaque color that a translucent color shows over a background."""
    weight = color.alphaF()
    return QColor(
        round(color.red() * weight + background.red() * (1.0 - weight)),
        round(color.green() * weight + background.green() * (1.0 - weight)),
        round(color.blue() * weight + background.blue() * (1.0 - weight)),
    )


def warning_text_color(background: QColor) -> QColor:
    """Return the warning amber with the higher contrast against the background."""
    candidates = (QColor(WARNING_ON_LIGHT), QColor(WARNING_ON_DARK))
    return max(candidates, key=lambda candidate: contrast_ratio(candidate, background))
