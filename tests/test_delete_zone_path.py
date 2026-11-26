#!/usr/bin/env python3
"""The zone delete helper removes only untracked paths inside the session's own zones."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import delete_zone_path

SESSION = "session-abc_123"
OTHER_SESSION = "session-other"
GIT_TIMEOUT_SECONDS = 15
EXIT_DELETED = 0
EXIT_WALK_REFUSED = 1
EXIT_INPUT_REFUSED = 2
POSIX_ONLY = "Descriptor-relative deletion needs POSIX dir_fd support"


@unittest.skipUnless(delete_zone_path.safe_deletion_supported(), POSIX_ONLY)
class DeleteZonePathTest(unittest.TestCase):
    """delete_zone_paths deletes inside the zone and refuses every other target."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       timeout=GIT_TIMEOUT_SECONDS)
        self.session_dir = self.root / ".scratch" / SESSION
        (self.session_dir / "run1" / "nested").mkdir(parents=True)
        (self.session_dir / "run1" / "nested" / "file.txt").write_text("x", encoding="utf-8")
        (self.session_dir / "a.txt").write_text("x", encoding="utf-8")
        (self.root / ".scratch" / OTHER_SESSION / "x").mkdir(parents=True)
        self.outside = Path(temporary.name).resolve() / "outside"
        (self.outside / "victim").mkdir(parents=True)
        (self.outside / "victim" / "keep.txt").write_text("x", encoding="utf-8")

    def delete(self, *operands: str, session_id: str = SESSION) -> int:
        return delete_zone_path.delete_zone_paths(str(self.root), session_id, list(operands))

    def test_deletes_file_and_tree(self) -> None:
        code = self.delete(f".scratch/{SESSION}/a.txt", f".scratch/{SESSION}/run1")
        self.assertEqual(code, EXIT_DELETED)
        self.assertFalse((self.session_dir / "a.txt").exists())
        self.assertFalse((self.session_dir / "run1").exists())
        self.assertTrue(self.session_dir.is_dir())

    def test_middle_link_refuses(self) -> None:
        link = self.session_dir / "swapped"
        link.symlink_to(self.outside)
        code = self.delete(f".scratch/{SESSION}/swapped/victim")
        self.assertEqual(code, EXIT_WALK_REFUSED)
        self.assertTrue((self.outside / "victim" / "keep.txt").exists())

    def test_session_folder_link_refuses(self) -> None:
        other = self.root / ".scratch" / "moved"
        self.session_dir.rename(other)
        self.session_dir.symlink_to(self.outside)
        code = self.delete(f".scratch/{SESSION}/victim")
        self.assertEqual(code, EXIT_WALK_REFUSED)
        self.assertTrue((self.outside / "victim" / "keep.txt").exists())

    def test_final_link_removes_link_only(self) -> None:
        link = self.session_dir / "pointer"
        link.symlink_to(self.outside / "victim")
        self.assertEqual(self.delete(f".scratch/{SESSION}/pointer"), EXIT_DELETED)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue((self.outside / "victim" / "keep.txt").exists())

    def test_invalid_targets_refuse(self) -> None:
        for operands in ((".scratch",), (f".scratch/{SESSION}",), (f".scratch/{OTHER_SESSION}/x",),
                         (f".scratch/{SESSION}/../{OTHER_SESSION}",), (str(self.outside),),
                         (f".scratch/{SESSION}/*",), (f".scratch/{SESSION}/run1/",),
                         ("hooks/x",), (f".scratch/{SESSION}/a.txt", "hooks/x"), ()):
            with self.subTest(operands=operands):
                self.assertEqual(self.delete(*operands), EXIT_INPUT_REFUSED)
                self.assertTrue((self.session_dir / "a.txt").exists())
                self.assertTrue((self.root / ".scratch" / OTHER_SESSION / "x").exists())

    def test_bad_session_refuses(self) -> None:
        for session_id in ("", "../x", "a b", "x" * 129):
            with self.subTest(session_id=session_id):
                code = self.delete(f".scratch/{SESSION}/a.txt", session_id=session_id)
                self.assertEqual(code, EXIT_INPUT_REFUSED)
        self.assertTrue((self.session_dir / "a.txt").exists())

    def test_tracked_file_refuses(self) -> None:
        subprocess.run(["git", "-C", str(self.root), "add", "-f", f".scratch/{SESSION}/a.txt"],
                       check=True, timeout=GIT_TIMEOUT_SECONDS)
        self.assertEqual(self.delete(f".scratch/{SESSION}/a.txt"), EXIT_INPUT_REFUSED)
        self.assertTrue((self.session_dir / "a.txt").exists())

    def test_linked_worktree_refuses(self) -> None:
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True,
                       timeout=GIT_TIMEOUT_SECONDS)
        worktree = self.root / ".worktrees" / SESSION / "feat"
        subprocess.run([*git, "worktree", "add", "-q", "-b", "feat", str(worktree)], check=True,
                       timeout=GIT_TIMEOUT_SECONDS)
        (worktree / "work.txt").write_text("uncommitted", encoding="utf-8")
        self.assertEqual(self.delete(f".worktrees/{SESSION}/feat"), EXIT_INPUT_REFUSED)
        self.assertTrue((worktree / "work.txt").exists())

    def test_nested_git_dir_refuses(self) -> None:
        (self.session_dir / "run1" / "nested" / ".git").mkdir()
        self.assertEqual(self.delete(f".scratch/{SESSION}/run1"), EXIT_INPUT_REFUSED)
        self.assertTrue((self.session_dir / "run1" / "nested" / "file.txt").exists())

    def test_git_component_refuses(self) -> None:
        (self.session_dir / "run1" / ".git").mkdir()
        (self.session_dir / "run1" / ".git" / "x").write_text("x", encoding="utf-8")
        self.assertEqual(self.delete(f".scratch/{SESSION}/run1/.git/x"), EXIT_INPUT_REFUSED)
        self.assertTrue((self.session_dir / "run1" / ".git" / "x").exists())

    def test_success_reports_each_operand(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.delete(f".scratch/{SESSION}/a.txt", f".scratch/{SESSION}/run1")
        self.assertEqual(code, EXIT_DELETED)
        self.assertEqual(output.getvalue().splitlines(),
                         ["delete_zone_path: deleted operand 1", "delete_zone_path: deleted operand 2"])

    def test_partial_failure_reports_deleted(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = self.delete(f".scratch/{SESSION}/a.txt", f".scratch/{SESSION}/a.txt")
        self.assertEqual(code, EXIT_WALK_REFUSED)
        self.assertFalse((self.session_dir / "a.txt").exists())
        self.assertEqual(output.getvalue().splitlines(), ["delete_zone_path: deleted operand 1"])
        self.assertIn("deleted 1 of 2 operands first", errors.getvalue())

    def test_missing_target_refuses(self) -> None:
        self.assertEqual(self.delete(f".scratch/{SESSION}/absent"), EXIT_WALK_REFUSED)


class SupportTest(unittest.TestCase):
    """The helper refuses before any work where descriptor deletion is unavailable."""

    def test_unsupported_platform_refuses(self) -> None:
        with patch.object(delete_zone_path.os, "supports_dir_fd", set()):
            self.assertFalse(delete_zone_path.safe_deletion_supported())
            code = delete_zone_path.delete_zone_paths(".", SESSION, [f".scratch/{SESSION}/a"])
        self.assertEqual(code, EXIT_INPUT_REFUSED)

    def test_rmtree_without_dir_fd_refuses(self) -> None:
        def legacy_rmtree(path, ignore_errors=False, onerror=None):
            raise AssertionError("legacy rmtree must never run")
        legacy_rmtree.avoids_symlink_attacks = True
        with patch.object(delete_zone_path.shutil, "rmtree", legacy_rmtree):
            self.assertFalse(delete_zone_path.safe_deletion_supported())
            code = delete_zone_path.delete_zone_paths(".", SESSION, [f".scratch/{SESSION}/a"])
        self.assertEqual(code, EXIT_INPUT_REFUSED)

    def test_unreadable_rmtree_signature_refuses(self) -> None:
        with patch.object(delete_zone_path.inspect, "signature", side_effect=ValueError("opaque")):
            self.assertFalse(delete_zone_path.safe_deletion_supported())

    def test_cli_requires_session_option(self) -> None:
        self.assertEqual(delete_zone_path.main([f".scratch/{SESSION}/a"]), EXIT_INPUT_REFUSED)
        self.assertEqual(delete_zone_path.main(["--session"]), EXIT_INPUT_REFUSED)


if __name__ == "__main__":
    unittest.main()
