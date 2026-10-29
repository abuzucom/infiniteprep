"""Pin no-follow metadata reads and repository PATH lookups in the hooks.

A repository writer controls the files these hooks read and the directories a
repository-local PATH entry exposes, so both checks have to hold against an
agent that edits the tree between the check and the use.
"""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_command_execution_gate import _load_gate_module
from tests.test_enforce_branch_name import _load_hook_module

READ_LIMIT = 64
WORKFLOW_CONSENT = "Repository workflow requires execution consent"
PROGRAM_NAMES = ("make", "rg", "python", "npm")


def _write_programs(directory: Path, names=PROGRAM_NAMES) -> None:
    """Place inert launchers where shutil.which finds them."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        for filename in (name, f"{name}.cmd"):
            launcher = directory / filename
            launcher.write_text("exit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _can_symlink() -> bool:
    """Return whether the current platform and user can create symlinks."""
    try:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.write_text("x", encoding="utf-8")
            (Path(temporary) / "link").symlink_to(target)
            return True
    except OSError:
        return False


CAN_SYMLINK = _can_symlink()


class TemporaryTree(unittest.TestCase):
    """Create one resolved temporary directory per test."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()


class ReadRegularTest(TemporaryTree):
    """Read bounded metadata without following a swapped symlink."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = _load_hook_module()

    def write(self, name: str, content: str) -> Path:
        """Write one fixture file."""
        path = self.base / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_reads_regular_file_within_limit(self) -> None:
        path = self.write("HEAD", "ref: refs/heads/fix/example\n")
        self.assertEqual(self.hook._read_regular(str(path), READ_LIMIT), "ref: refs/heads/fix/example\n")

    @unittest.skipUnless(CAN_SYMLINK, "Symlinks require elevated privileges on Windows")
    def test_symlink_leaf_raises(self) -> None:
        target = self.write("target", "x")
        link = self.base / "link"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            self.hook._read_regular(str(link), READ_LIMIT)

    def test_swapped_file_raises(self) -> None:
        path = self.write("HEAD", "ref: refs/heads/fix/example\n")
        other = os.stat(self.write("other", "y"))
        with patch.object(self.hook.os, "fstat", return_value=other):
            with self.assertRaisesRegex(OSError, "changed while the hook read it"):
                self.hook._read_regular(str(path), READ_LIMIT)

    def test_growth_after_lstat_raises(self) -> None:
        path = self.write("HEAD", "x" * (READ_LIMIT * 2))
        real = os.lstat(path)
        fields = list(real[:10])
        fields[stat.ST_SIZE] = 1
        reported = os.stat_result(fields)
        with patch.object(self.hook.os, "lstat", return_value=reported):
            with self.assertRaisesRegex(OSError, "bounded regular file"):
                self.hook._read_regular(str(path), READ_LIMIT)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs need POSIX")
    def test_fifo_raises_without_hanging(self) -> None:
        fifo = self.base / "HEAD"
        os.mkfifo(fifo)
        with self.assertRaises(OSError):
            self.hook._read_regular(str(fifo), READ_LIMIT)


class RepositoryProgramPathTest(TemporaryTree):
    """Name a repository-resolved program in the consent prompt."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def setUp(self) -> None:
        super().setUp()
        self.root = self.base / "repository"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "scripts" / "run_tests.py").write_text("", encoding="utf-8")
        self.outside = self.base / "tools"
        _write_programs(self.outside)

    def decide(self, command: str, *path_entries: Path) -> tuple:
        """Return the gate decision with PATH limited to the given entries."""
        search_path = os.pathsep.join(str(entry) for entry in path_entries)
        with patch.dict(os.environ, {"PATH": search_path}):
            return self.gate.workflow_decision(command, str(self.root))

    def test_outside_program_keeps_the_plain_prompt(self) -> None:
        for command in ("make lint PYTHON=python", "rg -n x README.md", "python scripts/run_tests.py"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command, self.outside), ("ask", WORKFLOW_CONSENT))

    def test_missing_program_keeps_the_plain_prompt(self) -> None:
        self.assertEqual(self.decide("make lint PYTHON=python", self.base / "empty"),
                         ("ask", WORKFLOW_CONSENT))

    def test_repository_program_is_named_in_the_prompt(self) -> None:
        cases = (("make lint PYTHON=python", "bin", "make"), ("rg -n x README.md", "bin", "rg"),
                 ("python scripts/run_tests.py", ".venv/bin", "python"))
        for command, directory, program in cases:
            with self.subTest(command=command):
                planted = self.root / directory
                _write_programs(planted, (program,))
                decision, reason = self.decide(command, planted, self.outside)
                self.assertEqual(decision, "ask", reason)
                self.assertIn(f"{directory}/{program}", reason.replace("\\", "/"))
                self.assertIn("inside the repository", reason)

    def test_repository_npm_still_denies(self) -> None:
        (self.root / "package.json").write_text('{"scripts": {"test": "x"}}', encoding="utf-8")
        planted = self.root / "bin"
        _write_programs(planted, ("npm",))
        decision, reason = self.decide("npm test", planted, self.outside)
        self.assertEqual(decision, "deny", reason)
        self.assertIn("inside the repository", reason)

    def test_bare_npm_is_not_a_workflow(self) -> None:
        self.assertEqual(self.decide("npm", self.outside), ("", ""))


if __name__ == "__main__":
    unittest.main()
