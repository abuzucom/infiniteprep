"""Behavioral checks for tool-name matching and pattern edge cases in the bundle check."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = ROOT / "packaging" / "check_bundle.py"


def _load_checker() -> ModuleType:
    """Import packaging/check_bundle.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("check_bundle_tools_target", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


class LauncherNameTest(unittest.TestCase):
    """A tool name fails behind a launcher suffix, the wrapper prefix, or as a folder."""

    def test_launcher_suffixes_and_the_wrapper_prefix_are_denied(self) -> None:
        """Scripts and wrappers named after a tool fail the check."""
        for name in (
            "_internal/rcc.sh",
            "_internal/uic.bat",
            "_internal/lupdate.cmd",
            "_internal/balsam.py",
            "_internal/pyside6-designer",
            "_internal/pyside6-rcc.exe",
        ):
            with self.subTest(name=name):
                self.assertTrue(CHECKER.is_denied(name))

    def test_files_inside_a_tool_folder_are_denied(self) -> None:
        """A file below a folder named after a tool fails, whatever its own name."""
        self.assertTrue(CHECKER.is_denied("_internal/Designer/readme.txt"))
        self.assertTrue(CHECKER.is_denied("_internal/qmlls/data/index.json"))

    def test_data_files_and_near_names_stay_allowed(self) -> None:
        """Data suffixes and longer names are not tool names."""
        for name in (
            "_internal/rcc.dat",
            "_internal/uic.json",
            "_internal/designerhelp.txt",
            "_internal/PySide6/Qt6Qml.dll",
            "_internal/libQt6QmlModels.so.6",
        ):
            with self.subTest(name=name):
                self.assertFalse(CHECKER.is_denied(name))


class PatternEdgeTest(unittest.TestCase):
    """Patterns that match nothing fail fast, and '**/' has no nested quantifier."""

    def test_trailing_slash_is_an_error(self) -> None:
        """A pattern ending in '/' raises ValueError, '**/' alone included."""
        for pattern in ("_internal/foo/", "**/"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                CHECKER.compile_pattern(pattern)

    def test_leading_double_star_matches_with_an_unquantified_group(self) -> None:
        """'**/' still matches zero or more folders, and its group carries no quantifier."""
        pattern = CHECKER.compile_pattern("**/*.qm")
        self.assertTrue(pattern.fullmatch("qt_de.qm"))
        self.assertTrue(pattern.fullmatch("a/b/qt_de.qm"))
        self.assertFalse(pattern.fullmatch("a/b/qt_de.dll"))
        self.assertNotIn(")?", pattern.pattern)
        self.assertNotIn(")*", pattern.pattern)


if __name__ == "__main__":
    unittest.main()
