#!/usr/bin/env python3
"""The .worktrees zone stays ignored and never enters a commit or diff."""
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_forbidden_paths

GIT_TIMEOUT_SECONDS = 15


class WorktreeZoneTest(unittest.TestCase):
    """Both session zones are forbidden in diffs and ignored by Git."""

    def test_worktree_path_forbidden(self):
        paths = [".worktrees/session/a.txt", ".worktrees/x", "worktrees/a.txt", "docs/.worktrees"]
        self.assertEqual(check_forbidden_paths.forbidden_paths(paths),
                         [".worktrees/session/a.txt", ".worktrees/x"])

    def test_both_zones_ignored(self):
        for path in (".scratch/session/a.txt", ".worktrees/session/a.txt"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q",
                                         "--no-index", path], check=False,
                                        timeout=GIT_TIMEOUT_SECONDS)
                self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
