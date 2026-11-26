#!/usr/bin/env python3
"""Test pre-merge safety preconditions."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_merge_safety as checker


class MergeSafetyFixture:
    """Build one fixture repository and run the checks against it."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self._git("init", "--quiet", "--initial-branch=feat/sample")
        self._git("config", "user.name", "Fixture")
        self._git("config", "user.email", "fixture@example.com")
        (self.root / "tracked.txt").write_text("seed\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "--quiet", "-m", "chore: seed fixture")

    def _git(self, *args: str) -> str:
        """Run one Git command in the fixture repository."""
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            self.fail(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout

    def _git_directory(self) -> Path:
        """Return the fixture repository administration directory."""
        return Path(self._git("rev-parse", "--absolute-git-dir").strip())

    def _verify(self, target: str = "origin/main", **kwargs):
        """Verify preconditions against the fixture repository."""
        return checker.verify_merge_safety(
            target, repo_root=self.root, scripts_dir=REPO_ROOT / "scripts", **kwargs
        )


class TargetSelectionTest(MergeSafetyFixture, unittest.TestCase):
    """Verify the merge target selection rules."""

    def test_empty_target_reference_fails(self) -> None:
        code, errors = self._verify("")
        self.assertEqual(code, 1)
        self.assertTrue(any("target reference is empty" in error for error in errors))

    def test_self_merge_fails(self) -> None:
        code, errors = self._verify("feat/sample")
        self.assertEqual(code, 1)
        self.assertTrue(
            any("cannot merge branch into itself" in error for error in errors)
        )


class GitOperationTest(MergeSafetyFixture, unittest.TestCase):
    """Verify that an active Git operation blocks a merge."""

    def test_merge_in_progress_fails(self) -> None:
        (self._git_directory() / "MERGE_HEAD").write_text("abc123\n", encoding="utf-8")
        code, errors = self._verify()
        self.assertEqual(code, 1)
        self.assertTrue(any("MERGE_HEAD" in error for error in errors))

    def test_rebase_in_progress_fails(self) -> None:
        (self._git_directory() / "rebase-merge").mkdir()
        code, errors = self._verify()
        self.assertEqual(code, 1)
        self.assertTrue(any("rebase" in error for error in errors))


class WorktreeStateTest(MergeSafetyFixture, unittest.TestCase):
    """Verify the worktree content rules."""

    def test_conflict_markers_fail(self) -> None:
        conflicted = self.root / "conflicted.txt"
        conflicted.write_text(
            "<<<<<<< HEAD\none\n=======\ntwo\n>>>>>>> origin/main\n",
            encoding="utf-8",
        )
        self._git("add", "conflicted.txt")
        code, errors = self._verify()
        self.assertEqual(code, 1)
        self.assertTrue(any("conflict markers present" in error for error in errors))

    def test_dirty_worktree_fails(self) -> None:
        (self.root / "tracked.txt").write_text("changed\n", encoding="utf-8")
        code, errors = self._verify()
        self.assertEqual(code, 1)
        self.assertTrue(any("worktree is not clean" in error for error in errors))

    def test_untracked_files_do_not_block(self) -> None:
        (self.root / "scratch.txt").write_text("scratch\n", encoding="utf-8")
        code, errors = self._verify()
        self.assertEqual(code, 1)
        self.assertFalse(any("worktree is not clean" in error for error in errors))


class BranchPreflightTest(MergeSafetyFixture, unittest.TestCase):
    """Verify the branch naming precondition."""

    def test_primary_branch_fails_preflight(self) -> None:
        self._git("branch", "-m", "master")
        code, errors = self._verify()
        self.assertEqual(code, 1)
        self.assertTrue(any("branch preflight failed" in error for error in errors))


class RepositoryCheckTest(MergeSafetyFixture, unittest.TestCase):
    """Verify the repository-wide preconditions."""

    def test_gate_adoption_failure_blocks_merge(self) -> None:
        with patch.object(
            checker, "_gate_adoption", return_value=(False, "gate adoption incomplete")
        ):
            code, errors = self._verify()
        self.assertEqual(code, 1)
        self.assertTrue(any("gate adoption incomplete" in error for error in errors))

    def test_policy_sync_failure_blocks_merge(self) -> None:
        with patch.object(
            checker, "_policy_sync", return_value=(False, "policy copies differ")
        ):
            code, errors = self._verify()
        self.assertEqual(code, 1)
        self.assertTrue(any("policy copies" in error for error in errors))

    def test_test_suite_failure_blocks_merge(self) -> None:
        with patch.object(
            checker, "_test_suite", return_value=(False, "one test failed")
        ):
            code, errors = self._verify(run_tests=True)
        self.assertEqual(code, 1)
        self.assertTrue(any("test suite failed" in error for error in errors))

    def test_all_preconditions_met_passes(self) -> None:
        with patch.object(
            checker, "_gate_adoption", return_value=(True, "")
        ), patch.object(checker, "_policy_sync", return_value=(True, "")):
            code, errors = self._verify()
        self.assertEqual(code, 0)
        self.assertEqual(errors, [])


class CommandLineTest(unittest.TestCase):
    """Verify the command line exit codes."""

    def test_cli_reports_failure(self) -> None:
        with patch.object(checker, "verify_merge_safety", return_value=(1, ["no target"])):
            self.assertEqual(checker.main(["origin/main"]), 1)

    def test_cli_reports_success(self) -> None:
        with patch.object(checker, "verify_merge_safety", return_value=(0, [])):
            self.assertEqual(checker.main(["origin/main"]), 0)


if __name__ == "__main__":
    unittest.main()
