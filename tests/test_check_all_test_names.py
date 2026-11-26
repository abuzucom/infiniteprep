#!/usr/bin/env python3
"""Tests for the --all mode of scripts/check_test_names.py.

Each case writes files into a throwaway tree and runs the checker as a
process over that tree.
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = REPO_ROOT / "scripts" / "check_test_names.py"

SHORT_TEST = """def test_row_height():
    pass
"""
LONG_TEST = """def test_rows_are_twenty_px_at_standard_size():
    pass
"""
NO_UNDERSCORE_TEST = """def testRowHeight():
    pass
"""
PRONOUN_COMMENT_TEST = """def test_row_height():
    # We check the row height here.
    pass
"""
THIRD_PERSON_COMMENT_TEST = """def test_row_height():
    # They check the row height.
    pass
"""
MARKER_COMMENT_TEST = """def test_row_height():
    # Now build the fixture.
    pass
"""
PLAIN_COMMENT_TEST = """def test_row_height():
    # The fixture holds one row.
    pass
"""
STRING_PRONOUN_TEST = """def test_row_height():
    message = "we check this"
    assert message
"""
BROKEN_TEST = """def test_x(:
"""
HASH_THEN_COMMENT_TEST = """def test_row_height():
    marker = "#"  # We check the marker.
    assert marker
"""
HASH_IN_STRING_TEST = """def test_row_height():
    marker = "# we keep this text"
    assert marker
"""
ESCAPE_CHARACTER = chr(27)
CONTROL_COMMENT_TEST = f"""def test_row_height():
    # We check {ESCAPE_CHARACTER}[31m red text.
    pass
"""
# One byte over the checker cap of 4 MiB.
OVERSIZE_BYTES = 4 * 1024 * 1024 + 1


def load_checker():
    """Import the checker by path, since scripts/ is not a package here."""
    spec = importlib.util.spec_from_file_location("check_test_names", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AllModeTest(unittest.TestCase):
    """The --all mode scans every test file in a tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write(self, relative_path: str, content: str) -> None:
        """Write one file below the throwaway tree."""
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _run_checker(self, *extra: str, repo: Path = None) -> subprocess.CompletedProcess:
        """Run the checker in --all mode and return the process result."""
        target = repo or self.root
        return subprocess.run(
            [sys.executable, str(CHECKER_PATH), "--all", "--repo", str(target), *extra],
            capture_output=True, text=True, check=False,
        )

    def test_short_names_pass(self):
        self._write("tests/test_rows.py", SHORT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_long_name_fails(self):
        self._write("tests/test_rows.py", LONG_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_rows.py:1", result.stdout)

    def test_missing_underscore_fails(self):
        self._write("tests/test_rows.py", NO_UNDERSCORE_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("testRowHeight", result.stdout)

    def test_pronoun_comment_fails(self):
        self._write("tests/test_rows.py", PRONOUN_COMMENT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_rows.py:2", result.stdout)
        self.assertIn("narration", result.stdout)

    def test_third_person_comment_fails(self):
        self._write("tests/test_rows.py", THIRD_PERSON_COMMENT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_rows.py:2", result.stdout)

    def test_marker_comment_fails(self):
        self._write("tests/test_rows.py", MARKER_COMMENT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_rows.py:2", result.stdout)

    def test_plain_comment_passes(self):
        self._write("tests/test_rows.py", PLAIN_COMMENT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_string_pronoun_ignored(self):
        self._write("tests/test_rows.py", STRING_PRONOUN_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_comment_after_string_hash(self):
        self._write("tests/test_rows.py", HASH_THEN_COMMENT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_rows.py:2", result.stdout)

    def test_string_hash_ignored(self):
        self._write("tests/test_rows.py", HASH_IN_STRING_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_repo_fails(self):
        result = self._run_checker(repo=self.root / "absent")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not exist", result.stderr)

    def test_missing_tests_folder_fails(self):
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no tests folder", result.stderr)

    def test_symlinked_tests_folder_fails(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        Path(outside.name, "test_rows.py").write_text(SHORT_TEST, encoding="utf-8")
        (self.root / "tests").symlink_to(outside.name, target_is_directory=True)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("symbolic link", result.stderr)

    def test_control_characters_escaped(self):
        self._write("tests/test_rows.py", CONTROL_COMMENT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(ESCAPE_CHARACTER, result.stdout)
        self.assertIn("\\x1b", result.stdout)

    def test_file_budget_enforced(self):
        self._write("tests/test_one.py", SHORT_TEST)
        self._write("tests/test_two.py", SHORT_TEST)
        checker = load_checker()
        checker.MAX_TREE_FILES = 1
        with self.assertRaisesRegex(ValueError, "file limit"):
            checker.find_tree_violations(str(self.root))

    def test_byte_budget_enforced(self):
        self._write("tests/test_one.py", SHORT_TEST)
        self._write("tests/test_two.py", SHORT_TEST)
        checker = load_checker()
        checker.MAX_TREE_BYTES = len(SHORT_TEST.encode("utf-8")) + 1
        with self.assertRaisesRegex(ValueError, "byte limit"):
            checker.find_tree_violations(str(self.root))

    def test_outside_tests_ignored(self):
        self._write("tests/test_rows.py", SHORT_TEST)
        self._write("scripts/helper.py", LONG_TEST + PRONOUN_COMMENT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_syntax_error_fails(self):
        self._write("tests/test_broken.py", BROKEN_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("test_broken.py", result.stderr)

    def test_oversize_file_fails(self):
        self._write("tests/test_huge.py", "#" * OVERSIZE_BYTES)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("byte cap", result.stderr)

    def test_all_with_range_rejected(self):
        result = self._run_checker("--base", "HEAD")
        self.assertEqual(result.returncode, 2)

    def test_repository_tree_passes(self):
        result = self._run_checker(repo=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
