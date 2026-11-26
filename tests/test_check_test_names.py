#!/usr/bin/env python3
"""Tests for scripts/check_test_names.py.

Each case builds a throwaway Git repository and runs the checker as a
process over a real base..head range.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = REPO_ROOT / "scripts" / "check_test_names.py"

IDENTITY = (
    "-c", "user.name=Test",
    "-c", "user.email=1+test@users.noreply.github.com",
)

SHORT_TEST = """def test_row_height():
    pass
"""
LONG_TEST = """def test_rows_are_twenty_px_at_standard_size():
    pass
"""
FIVE_WORD_TEST = """def test_a_b_c_d_e():
    pass
"""
NESTED_TEST = """class Outer:
    class Inner:
        async def test_one_two_three_four_five_six(self):
            pass
"""
BROKEN_TEST = """def test_x(:
"""
CLASS_A_LONG_TEST = """class First:
    def test_rows_are_twenty_px_at_standard_size(self):
        pass
"""
CLASS_B_LONG_TEST = """class Second:
    def test_rows_are_twenty_px_at_standard_size(self):
        pass
"""
TWO_CLASS_LONG_TEST = CLASS_A_LONG_TEST + CLASS_B_LONG_TEST
NO_UNDERSCORE_TEST = """def testRowHeight():
    pass
"""
# One byte over the checker cap of 4 MiB.
OVERSIZE_BYTES = 4 * 1024 * 1024 + 1


class RangeCheckTest(unittest.TestCase):
    """The checker flags long test names added in base..head."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self._git("init", "-b", "main")
        self._commit("tests/test_base.py", SHORT_TEST)
        self.base = self._git("rev-parse", "HEAD")

    def _git(self, *args) -> str:
        """Run Git in the throwaway repository and return its output."""
        result = subprocess.run(
            ["git", *IDENTITY, *args],
            cwd=self.repo, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    def _commit(self, relative_path: str, content: str) -> None:
        """Write one file and commit it."""
        target = self.repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._git("add", relative_path)
        self._git("commit", "-m", f"test: write {relative_path}")

    def _run_checker(self) -> subprocess.CompletedProcess:
        """Run the checker over base..HEAD and return the process result."""
        return subprocess.run(
            [sys.executable, str(CHECKER_PATH),
             "--base", self.base, "--head", "HEAD", "--repo", str(self.repo)],
            capture_output=True, text=True, check=False,
        )

    def test_long_new_name_fails(self):
        self._commit("tests/test_rows.py", LONG_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_rows.py:1", result.stdout)
        self.assertIn("test_rows_are_twenty_px_at_standard_size", result.stdout)

    def test_five_word_name_passes(self):
        self._commit("tests/test_rows.py", FIVE_WORD_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unchanged_long_name_passes(self):
        self._commit("tests/test_old.py", LONG_TEST)
        self.base = self._git("rev-parse", "HEAD")
        self._commit("tests/test_old.py", LONG_TEST + SHORT_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_long_rename_fails(self):
        self._commit("tests/test_base.py", LONG_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("test_rows_are_twenty_px_at_standard_size", result.stdout)

    def test_async_and_nested_counted(self):
        self._commit("tests/unit/test_nested.py", NESTED_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/unit/test_nested.py:3", result.stdout)

    def test_syntax_error_fails(self):
        self._commit("tests/test_broken.py", BROKEN_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_broken.py", result.stderr)

    def test_moved_long_name_passes(self):
        self._commit("tests/test_moved.py", CLASS_A_LONG_TEST)
        self.base = self._git("rev-parse", "HEAD")
        self._commit("tests/test_moved.py", CLASS_B_LONG_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_renamed_file_fails(self):
        self._commit("tests/test_old_path.py", LONG_TEST)
        self.base = self._git("rev-parse", "HEAD")
        self._git("mv", "tests/test_old_path.py", "tests/test_new_path.py")
        self._git("commit", "-m", "test: move test file")
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_new_path.py:1", result.stdout)

    def test_duplicate_long_name_fails(self):
        self._commit("tests/test_copied.py", CLASS_A_LONG_TEST)
        self.base = self._git("rev-parse", "HEAD")
        self._commit("tests/test_copied.py", TWO_CLASS_LONG_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_copied.py:5", result.stdout)
        self.assertNotIn("tests/test_copied.py:2", result.stdout)

    def test_missing_underscore_fails(self):
        self._commit("tests/test_prefix.py", NO_UNDERSCORE_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("testRowHeight", result.stdout)

    def test_oversize_blob_fails(self):
        oversize = "#" * OVERSIZE_BYTES
        self._commit("tests/test_huge.py", oversize)
        result = self._run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"holds {OVERSIZE_BYTES} bytes", result.stderr)
        self.assertIn("byte cap", result.stderr)

    def test_outside_tests_ignored(self):
        self._commit("scripts/helper.py", LONG_TEST)
        result = self._run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
