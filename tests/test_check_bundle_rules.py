"""Behavioral checks for the bundle check's pattern, denylist, and OS rules (D83a4a)."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = ROOT / "packaging" / "check_bundle.py"


def _load_checker() -> ModuleType:
    """Import packaging/check_bundle.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("check_bundle_rules_target", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


class DoubleStarRuleTest(unittest.TestCase):
    """'**' only starts a path part, and a bare '**' only ends the pattern."""

    def test_double_star_inside_a_part_is_an_error(self) -> None:
        """A '**' after other characters in a part raises ValueError."""
        for pattern in ("_internal/foo**bar", "_internal/lib**.so", "a**/b"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                CHECKER.compile_pattern(pattern)

    def test_bare_double_star_before_more_text_is_an_error(self) -> None:
        """A bare '**' that does not end the pattern raises ValueError."""
        with self.assertRaises(ValueError):
            CHECKER.compile_pattern("_internal/**x")

    def test_documented_double_star_forms_still_compile(self) -> None:
        """'**/', a trailing '/**', and '**' alone all compile and match as documented."""
        self.assertTrue(CHECKER.compile_pattern("**").fullmatch("a/b/c"))
        self.assertTrue(CHECKER.compile_pattern("_internal/**").fullmatch("_internal/a/b"))
        self.assertFalse(CHECKER.compile_pattern("_internal/**").fullmatch("other/a"))
        self.assertTrue(CHECKER.compile_pattern("**/*.qm").fullmatch("a/b/qt_de.qm"))


class ToolNameRuleTest(unittest.TestCase):
    """Tool names match whole file names, with or without '.exe'."""

    def test_data_files_sharing_a_tool_name_are_not_denied(self) -> None:
        """'rcc.dat' and 'uic.json' keep their suffix. Neither equals a tool name."""
        for name in ("_internal/rcc.dat", "_internal/uic.json", "_internal/designer.txt"):
            with self.subTest(name=name):
                self.assertFalse(CHECKER.is_denied(name))

    def test_any_part_named_after_a_tool_app_is_denied(self) -> None:
        """A '.app' part named after a tool fails, also as the last part."""
        self.assertTrue(CHECKER.is_denied("Designer.app"))
        self.assertTrue(CHECKER.is_denied("_internal/Linguist.app/Contents/Info.plist"))
        self.assertFalse(CHECKER.is_denied("_internal/InfinitePrep.app/Contents/Info.plist"))


class OperatingSystemRuleTest(unittest.TestCase):
    """check_bundle names the valid operating systems instead of a missing file."""

    def test_unknown_operating_system_raises_value_error(self) -> None:
        """An OS name outside the three allowlists raises ValueError before any read."""
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError) as caught:
            CHECKER.check_bundle(Path(temp), "solaris")

        self.assertIn("windows", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
