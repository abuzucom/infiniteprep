"""Load the bundled icons and render them in a theme color at any size.

The icons are unchanged SVG files from Tabler Icons v3.50.0 and Lucide 1.70.0. Both
sets draw strokes in currentColor, so a render swaps that keyword for a token color.
The Qt SVG image plugin renders the result, so no extra Qt module ships for icons.
"""

from __future__ import annotations

import functools
from enum import StrEnum
from pathlib import Path
from typing import Final

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QColor, QIcon, QImage, QImageReader, QPixmap


class IconSet(StrEnum):
    """The icon sets, named after the folders that hold their files."""

    TABLER = "tabler"
    LUCIDE = "lucide"


ICONS_DIR: Final[Path] = Path(__file__).resolve().parent / "icons"
SVG_FORMAT: Final[bytes] = b"svg"
CURRENT_COLOR: Final[bytes] = b"currentColor"
# QIcon picks the closest pixmap. These sizes cover menus, tables, and toolbars.
DEFAULT_ICON_SIZES: Final[tuple[int, ...]] = (16, 20, 24, 32)
# Enough for every icon in both themes' status colors and the plain text colors.
ICON_CACHE_SIZE: Final[int] = 256

TABLER_ICONS: Final[tuple[str, ...]] = (
    "adjustments",
    "arrow-down",
    "arrow-up",
    "arrows-exchange",
    "circle-dashed-check",
    "file-music",
    "fingerprint",
    "history",
    "lock",
    "lock-open",
    "logout",
    "metronome",
    "music",
    "pin",
    "refresh",
    "shield-x",
    "tag-plus",
    "usb",
    "vinyl",
    "zoom-in",
    "zoom-question",
)
LUCIDE_ICONS: Final[tuple[str, ...]] = (
    "audio-waveform",
    "ban",
    "check",
    "check-check",
    "circle-check",
    "circle-dashed",
    "circle-off",
    "circle-play",
    "circle-x",
    "clock-fading",
    "cloud-download",
    "copy",
    "database",
    "database-backup",
    "folder",
    "folder-input",
    "hard-drive",
    "loader",
    "microscope",
    "notebook-pen",
    "pause",
    "play",
    "replace",
    "save",
    "scan-eye",
    "settings",
    "square",
    "square-arrow-out-up-right",
    "tags",
    "triangle-exclamation-point",
    "turntable",
    "undo-2",
    "x",
)
# Every icon name maps to its set. No name appears in both sets.
ICON_SETS: Final[dict[str, IconSet]] = {
    **dict.fromkeys(TABLER_ICONS, IconSet.TABLER),
    **dict.fromkeys(LUCIDE_ICONS, IconSet.LUCIDE),
}


def icon_file_path(name: str) -> Path:
    """Return the path of a bundled icon. Raise ValueError for an unknown name."""
    icon_set = ICON_SETS.get(name)
    if icon_set is None:
        raise ValueError(f"No bundled icon is named '{name}'. Use a name from ICON_SETS.")
    return ICONS_DIR / icon_set.value / f"{name}.svg"


def colored_svg(name: str, color: str) -> bytes:
    """Return the icon SVG with its stroke color set. Raise ValueError for a bad color.

    QColor normalizes the color to #rrggbb before it enters the SVG text, so the
    color argument cannot inject markup.
    """
    parsed = QColor(color)
    if not parsed.isValid():
        raise ValueError(f"'{color}' is not a color. Pass a token hex value such as #38bdf8.")
    data = icon_file_path(name).read_bytes()
    return data.replace(CURRENT_COLOR, parsed.name().encode("ascii"))


def render_icon_image(name: str, color: str, size: int, device_pixel_ratio: float = 1.0) -> QImage:
    """Render an icon in a color at a logical size and a device pixel ratio.

    Raise ValueError for a size below 1 and RuntimeError when Qt cannot render the SVG.
    """
    if size < 1:
        raise ValueError(f"Icon sizes start at 1 logical pixel: {size}")
    physical_size = max(1, round(size * device_pixel_ratio))
    buffer = QBuffer()
    buffer.setData(QByteArray(colored_svg(name, color)))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, QByteArray(SVG_FORMAT))
    reader.setScaledSize(QSize(physical_size, physical_size))
    image = reader.read()
    buffer.close()
    if image.isNull():
        raise RuntimeError(
            f"Qt could not render the icon '{name}': {reader.errorString()}. "
            "Check that the Qt SVG image plugin ships with the app."
        )
    image.setDevicePixelRatio(device_pixel_ratio)
    return image


@functools.lru_cache(maxsize=ICON_CACHE_SIZE)
def cached_themed_icon(name: str, color: str) -> QIcon:
    """Return a shared QIcon for a name and color at the default sizes.

    Table cells repeat a few icons on every row. The cache renders each pair once.
    """
    return themed_icon(name, color)


def themed_icon(
    name: str,
    color: str,
    device_pixel_ratio: float = 1.0,
    sizes: tuple[int, ...] = DEFAULT_ICON_SIZES,
) -> QIcon:
    """Return a QIcon that holds the icon in one color at each of the given sizes."""
    icon = QIcon()
    for size in sizes:
        image = render_icon_image(name, color, size, device_pixel_ratio)
        icon.addPixmap(QPixmap.fromImage(image))
    return icon
