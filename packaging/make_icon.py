"""Icon converter for the AppImage and the Windows installer (D393).

build-packages runs it with the locked Pillow release:

    python packaging/make_icon.py png <source PNG> <output PNG>
    python packaging/make_icon.py ico <source PNG> <output ICO>

"png" writes the 256x256 AppImage icon. "ico" writes the multi-size installer icon.
PyInstaller converts the exe and macOS app icons itself.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import PIL
from PIL import Image

# The build trusts only the Pillow release that requirements-lock.txt pins.
EXPECTED_PILLOW_VERSION: Final[str] = "13.0.0"
# 256 is the largest Windows icon size and a size linuxdeploy accepts.
APPIMAGE_ICON_SIZE: Final[tuple[int, int]] = (256, 256)
ICO_SIZES: Final[tuple[tuple[int, int], ...]] = (
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
)
OUTPUT_FORMATS: Final[tuple[str, ...]] = ("png", "ico")


def require_pillow_version(installed: str) -> None:
    """Stop the build unless the installed Pillow matches the lockfile pin."""
    if installed != EXPECTED_PILLOW_VERSION:
        raise SystemExit(
            f"Pillow {installed} is installed, but the icon build needs {EXPECTED_PILLOW_VERSION}."
            " Install the pinned release with: python -m pip install -r requirements-lock.txt"
        )


def write_icon(output_format: str, source: Path, output: Path) -> None:
    """Write the source PNG as the AppImage PNG or the installer ICO."""
    with Image.open(source) as logo:
        rgba = logo.convert("RGBA")
    if output_format == "png":
        rgba.resize(APPIMAGE_ICON_SIZE, Image.Resampling.LANCZOS).save(output, format="PNG")
        return
    rgba.save(output, format="ICO", sizes=list(ICO_SIZES))


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(description="Convert the app icon for packaging.")
    parser.add_argument("output_format", choices=OUTPUT_FORMATS)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Check Pillow, convert the icon, and return 0."""
    arguments = build_parser().parse_args(argv)
    require_pillow_version(PIL.__version__)
    write_icon(arguments.output_format, arguments.source, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
