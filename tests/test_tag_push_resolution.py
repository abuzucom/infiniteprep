#!/usr/bin/env python3
"""Check that a push naming an existing local tag denies in any name shape."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))
import _gate_core

FIXTURE_OBJECT_ID = "0123456789abcdef0123456789abcdef01234567"
BLOCKING_EXIT_CODE = 2
TIMEOUT_SECONDS = 30


def build_repository(root: Path) -> Path:
    """Write a minimal repository with loose and packed tags and branches."""
    git_dir = root / ".git"
    for directory in ("refs/tags/team", "refs/heads", "objects"):
        (git_dir / directory).mkdir(parents=True, exist_ok=True)
    (git_dir / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
    (git_dir / "HEAD").write_text("ref: refs/heads/feat/x\n", encoding="utf-8")
    for ref in ("refs/tags/production", "refs/tags/team/stable", "refs/heads/v2-release"):
        (git_dir / ref).write_text(FIXTURE_OBJECT_ID + "\n", encoding="utf-8")
    (git_dir / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{FIXTURE_OBJECT_ID} refs/heads/feat/x\n"
        f"{FIXTURE_OBJECT_ID} refs/tags/packed-tag\n"
        f"^{FIXTURE_OBJECT_ID}\n",
        encoding="utf-8")
    return git_dir


def decide(command: str, cwd: Path) -> tuple:
    """Return the shared core verdict for one git command in a directory."""
    return _gate_core.git_verdict(command.split()[1:], str(cwd))


class LocalTagResolutionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git_dir = build_repository(self.root)

    def test_existing_tag_push_denies(self):
        for command in ("git push origin production",
                        "git push origin packed-tag",
                        "git push origin team/stable",
                        "git push origin production:refs/heads/feat/x",
                        "git push origin tags/anything"):
            with self.subTest(command=command):
                self.assertEqual(decide(command, self.root)[0], "deny")

    def test_version_shaped_branch_passes(self):
        self.assertEqual(decide("git push origin v2-release", self.root), ("", ""))

    def test_ordinary_branch_pushes_pass(self):
        for command in ("git push origin feat/x", "git push -u origin feat/x",
                        "git push origin HEAD:refs/heads/feat/x"):
            with self.subTest(command=command):
                self.assertEqual(decide(command, self.root), ("", ""))

    def test_remote_operand_not_tag(self):
        (self.git_dir / "refs" / "tags" / "origin").write_text(
            FIXTURE_OBJECT_ID + "\n", encoding="utf-8")
        self.assertEqual(decide("git push origin feat/x", self.root), ("", ""))

    def test_a_reftable_store_asks(self):
        (self.git_dir / "reftable").mkdir()
        self.assertEqual(decide("git push origin feat/x", self.root)[0], "ask")

    def test_an_unsafe_ref_name_asks(self):
        for command in ("git push origin ../../outside", "git push origin /etc/passwd"):
            with self.subTest(command=command):
                self.assertEqual(decide(command, self.root)[0], "ask")

    def test_mirror_keeps_asking(self):
        self.assertEqual(decide("git push --mirror origin", self.root)[0], "ask")

    def test_dash_c_repository_tags(self):
        outside = self.root / "outside"
        outside.mkdir()
        args = ["-C", str(self.root), "push", "origin", "production"]
        self.assertEqual(_gate_core.git_verdict(args, str(outside))[0], "deny")


class UpdateRefTagTest(unittest.TestCase):
    def test_update_ref_tags_denies(self):
        for args in (["update-ref", "refs/tags/x", "HEAD"],
                     ["update-ref", "-d", "refs/tags/x"],
                     ["update-ref", "REFS/TAGS/x", "HEAD"]):
            with self.subTest(args=args):
                self.assertEqual(_gate_core.git_verdict(args)[0], "deny")

    def test_update_ref_from_stdin_asks(self):
        self.assertEqual(_gate_core.git_verdict(["update-ref", "--stdin"])[0], "ask")

    def test_update_ref_branch_asks(self):
        self.assertEqual(
            _gate_core.git_verdict(["update-ref", "refs/heads/feat/x", "HEAD"])[0], "ask")


class ShellGateResolutionTest(unittest.TestCase):
    def test_bash_local_tag_push(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_repository(root)
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "permission_mode": "default", "cwd": str(root),
                       "tool_input": {"command": "git push origin production"}}
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "hooks" / "block_destructive_bash.py")],
                input=json.dumps(payload), capture_output=True, text=True,
                timeout=TIMEOUT_SECONDS, check=False)
        self.assertEqual(result.returncode, BLOCKING_EXIT_CODE, result.stdout)
        self.assertIn("tag", result.stderr)


if __name__ == "__main__":
    unittest.main()
