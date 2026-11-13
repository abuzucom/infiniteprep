"""Behavioral checks that the logo reaches the app, the packages, and the website (D393)."""

from __future__ import annotations

import re
import struct
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
APP_ICON_PATH = ROOT / "src/infiniteprep/ui/app-icon.png"
SPEC_PATH = ROOT / "infiniteprep.spec"
ALLOWLIST_DIR = ROOT / "packaging/bundle-allowlist"
ALLOWLIST_NAMES = ("linux.txt", "macos.txt", "windows.txt")
BUNDLED_ICON = "_internal/infiniteprep/ui/app-icon.png"
WORKFLOW_PATH = ROOT / ".github/workflows/build-packages.yml"
APPIMAGE_STEP = "Build Linux AppImage"
SITE_INDEX = ROOT / "website/index.html"
FAVICON_PATH = ROOT / "website/favicon.png"
PYPROJECT_PATH = ROOT / "pyproject.toml"
LOCKFILE_PATH = ROOT / "requirements-lock.txt"
PILLOW_PIN = "pillow==13.0.0"
ICON_LINK = re.compile(r'<link rel="icon" type="image/png" href="favicon\.png">')
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The IHDR chunk follows the signature. Width and height sit at bytes 16 to 24.
PNG_SIZE_OFFSET = 16
PNG_SIZE_END = 24
# 256 is the largest Windows .ico size and the AppImage icon size (D393).
APP_ICON_SIZE = (256, 256)
FAVICON_SIZE = (64, 64)


def read_png_size(path: Path) -> tuple[int, int]:
    """Return the width and height stored in a PNG header."""
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"{path} is not a PNG file")
    return struct.unpack(">II", data[PNG_SIZE_OFFSET:PNG_SIZE_END])


class AppIconWiringTest(unittest.TestCase):
    """The logo is the app icon, the packaged icon, and the favicon."""

    def test_the_app_icon_is_a_256_pixel_png(self) -> None:
        """The app ships the logo at the largest Windows icon size."""
        self.assertEqual(read_png_size(APP_ICON_PATH), APP_ICON_SIZE)

    def test_the_spec_bundles_the_icon_file(self) -> None:
        """The frozen app finds the icon beside the app_icon module."""
        spec = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn('(str(APP_ICON_SOURCE), "infiniteprep/ui")', spec)
        self.assertIn('APP_ICON_SOURCE = src_dir / "infiniteprep" / "ui" / "app-icon.png"', spec)

    def test_the_exe_and_app_bundle_use_the_logo(self) -> None:
        """PyInstaller converts the PNG for the Windows exe and the macOS app."""
        spec = SPEC_PATH.read_text(encoding="utf-8")
        self.assertEqual(spec.count("icon=str(APP_ICON_SOURCE),"), 2)

    def test_every_allowlist_admits_the_icon(self) -> None:
        """The bundle check accepts the icon on each platform."""
        for name in ALLOWLIST_NAMES:
            with self.subTest(allowlist=name):
                lines = (ALLOWLIST_DIR / name).read_text(encoding="utf-8").splitlines()
                self.assertIn(BUNDLED_ICON, lines)

    def test_the_appimage_icon_comes_from_the_logo(self) -> None:
        """The AppImage step resizes the logo instead of drawing a placeholder."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        steps: list[dict[str, Any]] = workflow["jobs"]["bundle"]["steps"]
        [step] = [step for step in steps if step.get("name") == APPIMAGE_STEP]
        run = step["run"]
        self.assertIn("src/infiniteprep/ui/app-icon.png", run)
        self.assertIn("from PIL import Image", run)
        self.assertNotIn('chunk(b"IHDR"', run)

    def test_pillow_is_a_pinned_build_dependency(self) -> None:
        """PyInstaller needs Pillow to convert the PNG icon."""
        self.assertIn(f'"{PILLOW_PIN}"', PYPROJECT_PATH.read_text(encoding="utf-8"))
        lock_lines = LOCKFILE_PATH.read_text(encoding="utf-8").splitlines()
        self.assertIn(PILLOW_PIN, lock_lines)

    def test_the_website_links_the_favicon(self) -> None:
        """The landing page names the 64-pixel logo as its favicon."""
        self.assertEqual(read_png_size(FAVICON_PATH), FAVICON_SIZE)
        self.assertRegex(SITE_INDEX.read_text(encoding="utf-8"), ICON_LINK)


if __name__ == "__main__":
    unittest.main()
