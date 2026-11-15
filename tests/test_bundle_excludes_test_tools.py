"""The bundles leave out pytest and pygments. Only the test suite uses those packages.

scipy imports pytest inside PytestTester. The pytest hook then pulls in all of pytest and
pygments. The app never calls the scipy test runner and never imports either package.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_bundle_excludes_pillow import imported_top_level_packages, read_spec_excludes

ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE_DIR = ROOT / "src" / "infiniteprep"
TEST_ONLY_PACKAGES = ("pytest", "_pytest", "pygments")


class SpecExcludesTestToolsTest(unittest.TestCase):
    """PyInstaller never collects pytest or pygments into a bundle."""

    def test_analysis_excludes_pytest_and_pygments(self) -> None:
        """The evaluated Analysis excludes list names every test-only package."""
        excludes = read_spec_excludes()
        for package in TEST_ONLY_PACKAGES:
            self.assertIn(package, excludes)

    def test_pil_stays_excluded(self) -> None:
        """The Pillow exclusion (D393) stays in place beside the new entries."""
        self.assertIn("PIL", read_spec_excludes())

    def test_app_code_never_imports_test_tools(self) -> None:
        """No module under src/infiniteprep imports pytest or pygments."""
        importers = sorted(
            f"{path.relative_to(ROOT)}: {package}"
            for path in APP_SOURCE_DIR.rglob("*.py")
            for package in TEST_ONLY_PACKAGES
            if package in imported_top_level_packages(path)
        )
        self.assertEqual(importers, [])


if __name__ == "__main__":
    unittest.main()
