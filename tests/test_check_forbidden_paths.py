#!/usr/bin/env python3
"""Test that untracked session zones never enter a commit or diff."""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_forbidden_paths

GIT_IDENTITY = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid")


def _git(root: Path, *arguments: str) -> str:
    """Run git in a fixture repository and return its standard output."""
    completed = subprocess.run(["git", "-C", str(root), *GIT_IDENTITY, *arguments],
                               capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _run(arguments: list) -> tuple:
    """Return the exit code and stderr text for one checker run."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
        code = check_forbidden_paths.main(arguments)
    return code, stderr.getvalue()


class ForbiddenPathRuleTest(unittest.TestCase):
    """Path classification needs no repository."""

    def test_scratch_path_forbidden(self):
        self.assertEqual(check_forbidden_paths.forbidden_paths([".scratch/a/HANDOFF.md"]),
                         [".scratch/a/HANDOFF.md"])

    def test_ordinary_paths_allowed(self):
        self.assertEqual(check_forbidden_paths.forbidden_paths(["README.md", "scratch/x"]), [])

    def test_control_characters_sanitized(self):
        self.assertEqual(check_forbidden_paths.sanitize_path(".scratch/a\nb\x1b"),
                         ".scratch/a?b?")


class ForbiddenPathRepositoryTest(unittest.TestCase):
    """The checker reads staged and committed paths from git."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        _git(self.root, "init", "-q")
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        _git(self.root, "add", "README.md")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = _git(self.root, "rev-parse", "HEAD")

    def _add_scratch_file(self) -> None:
        """Force-stage one file below the scratch zone."""
        target = self.root / ".scratch" / "session" / "HANDOFF.md"
        target.parent.mkdir(parents=True)
        target.write_text("status\n", encoding="utf-8")
        _git(self.root, "add", "-f", ".scratch/session/HANDOFF.md")

    def test_staged_scratch_file_fails(self):
        self._add_scratch_file()
        code, output = _run(["--root", str(self.root), "--staged"])
        self.assertEqual(code, 1)
        self.assertIn(".scratch/session/HANDOFF.md", output)

    def test_committed_scratch_file_fails(self):
        self._add_scratch_file()
        _git(self.root, "commit", "-q", "-m", "scratch")
        head = _git(self.root, "rev-parse", "HEAD")
        code, _output = _run(["--root", str(self.root), "--base", self.base, "--head", head])
        self.assertEqual(code, 1)

    def test_clean_diff_passes(self):
        (self.root / "README.md").write_text("changed\n", encoding="utf-8")
        _git(self.root, "add", "README.md")
        code, output = _run(["--root", str(self.root), "--staged"])
        self.assertEqual(code, 0, output)

    def test_option_revision_rejected(self):
        code, _output = _run(["--root", str(self.root), "--base=-oops", "--head", "HEAD"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
