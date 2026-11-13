"""Behavioral tests for the packaged icon converter (D393)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CONVERTER_PATH = ROOT / "packaging/make_icon.py"
APP_ICON_PATH = ROOT / "src/infiniteprep/ui/app-icon.png"
LOCKED_PILLOW = "13.0.0"
APPIMAGE_ICON_SIZE = (256, 256)
SMALLEST_ICO_SIZE = (16, 16)
USAGE_ERROR = 2


def load_converter() -> ModuleType:
    """Load packaging/make_icon.py from its exact path."""
    spec = importlib.util.spec_from_file_location("make_icon", CONVERTER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {CONVERTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PillowVersionTest(unittest.TestCase):
    """The converter runs only with the locked Pillow release."""

    def setUp(self) -> None:
        self.converter = load_converter()

    def test_the_locked_release_passes(self) -> None:
        """The lockfile pin passes the check."""
        self.converter.require_pillow_version(LOCKED_PILLOW)

    def test_another_release_stops_the_build(self) -> None:
        """Any other version stops with a message that names the lockfile."""
        with self.assertRaises(SystemExit) as raised:
            self.converter.require_pillow_version("12.9.0")
        message = str(raised.exception.code)
        self.assertIn("12.9.0", message)
        self.assertIn("requirements-lock.txt", message)

    def test_the_expected_release_matches_the_lock(self) -> None:
        """The converter expects the release that the lockfile pins."""
        lock_lines = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(f"pillow=={self.converter.EXPECTED_PILLOW_VERSION}", lock_lines)


class ConversionTest(unittest.TestCase):
    """The converter writes the AppImage PNG and the installer ICO."""

    def setUp(self) -> None:
        self.converter = load_converter()
        self.folder = Path(tempfile.mkdtemp())

    def test_png_mode_writes_a_256_pixel_icon(self) -> None:
        """The AppImage icon is a 256x256 PNG."""
        output = self.folder / "infiniteprep.png"
        self.assertEqual(self.converter.main(["png", str(APP_ICON_PATH), str(output)]), 0)
        with Image.open(output) as icon:
            self.assertEqual(icon.format, "PNG")
            self.assertEqual(icon.size, APPIMAGE_ICON_SIZE)

    def test_ico_mode_writes_every_installer_size(self) -> None:
        """The installer ICO holds sizes from 16 to 256 pixels."""
        output = self.folder / "installer.ico"
        self.assertEqual(self.converter.main(["ico", str(APP_ICON_PATH), str(output)]), 0)
        with Image.open(output) as icon:
            self.assertEqual(icon.format, "ICO")
            sizes = set(icon.info["sizes"])
        self.assertIn(SMALLEST_ICO_SIZE, sizes)
        self.assertIn(APPIMAGE_ICON_SIZE, sizes)

    def test_an_unknown_mode_is_a_usage_error(self) -> None:
        """A mode other than png or ico writes nothing and returns 2."""
        output = self.folder / "icon.bmp"
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.converter.main(["bmp", str(APP_ICON_PATH), str(output)])
        self.assertEqual(raised.exception.code, USAGE_ERROR)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
