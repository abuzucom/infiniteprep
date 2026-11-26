#!/usr/bin/env python3
"""Tree checker copies agree, and their Git lookups stay outside the repo (S6, S7, S-12, S-13).

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
SANITIZER_SAMPLES = ("plain text", "tab\there", "line\nbreak", "escape \x1b[31m red",
                     "caf\u00e9", "bidi \u202e mark", "\x00nul")
OBJECT_IDS = ("a" * 40, "B" * 64, "a" * 39, "g" * 40, "", "a" * 40 + "\n")
TREE_PATHS = (b"docs/readme.md", b"/absolute", b"a//b", b"a/./b", b"a/../b", b"", b"\xff\xfe")


def load_script(name: str):
    """Load one script by path under a private module name."""
    spec = importlib.util.spec_from_file_location(f"_parity_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outcome(function, value) -> tuple:
    """Return ("ok", result) or ("error", message) for one call."""
    try:
        return "ok", function(value)
    except RuntimeError as error:
        return "error", str(error)


class ValidatorParityTest(unittest.TestCase):
    """Both tree checkers validate object IDs and tree paths identically."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = load_script("check_compliance_tree")
        cls.markers = load_script("check_conflict_markers")

    def test_object_ids(self):
        for value in OBJECT_IDS:
            with self.subTest(value=value):
                self.assertEqual(outcome(self.tree._validate_object_id, value),
                                 outcome(self.markers._validate_object_id, value))

    def test_tree_paths(self):
        for value in TREE_PATHS:
            with self.subTest(value=value):
                self.assertEqual(outcome(self.tree._validate_tree_path, value),
                                 outcome(self.markers._validate_tree_path, value))

    def test_sanitizers_agree_below_both_limits(self):
        for value in SANITIZER_SAMPLES:
            with self.subTest(value=value):
                self.assertEqual(self.tree._sanitize(value), self.markers._sanitize(value))


class ConflictReportTest(unittest.TestCase):
    """The conflict checker report escapes untrusted violation text."""

    def test_report_sanitizes_each_line(self):
        markers = load_script("check_conflict_markers")
        with patch.object(sys, "stderr", new_callable=lambda: _Capture()) as stderr:
            code = markers._report(["file\x1b[2J.md:1: unclosed conflict marker opener"])
        self.assertEqual(code, 1)
        self.assertNotIn("\x1b", stderr.text)
        self.assertIn("\\x1b", stderr.text)

    def test_decode_error_sanitized(self):
        markers = load_script("check_conflict_markers")
        # Windows rejects a control character in a file name, so the read
        # is replaced and the hostile path stays a plain string.
        with patch.object(markers, "_safe_read", return_value=(b"text\n", None)), \
                patch.object(markers, "decode_content", return_value=(None, "bad\x1b[31m")):
            violations = markers.check_file("name\x1b.txt")
        self.assertEqual(len(violations), 1)
        self.assertNotIn("\x1b", violations[0])


class ConflictCliTest(unittest.TestCase):
    """The conflict checker accepts --tree and --repo once each."""

    def test_duplicate_options_are_rejected(self):
        markers = load_script("check_conflict_markers")
        tree_id = "a" * 40
        for raw_args in (["--tree", tree_id, "--tree", tree_id, "--repo", "."],
                         ["--repo", ".", "--repo", ".", "--tree", tree_id]):
            with self.subTest(raw_args=raw_args):
                with self.assertRaisesRegex(RuntimeError, "may be supplied only once"):
                    markers._parse_cli(raw_args)

    def test_tree_and_repo_are_recorded(self):
        markers = load_script("check_conflict_markers")
        tree_id = "a" * 40
        parsed = markers._parse_cli(["--tree", tree_id, "--repo", "."])
        self.assertEqual(parsed, (False, [], tree_id, "."))


class _Capture:
    """A minimal text stream that keeps everything written to it."""

    def __init__(self) -> None:
        self.text = ""

    def write(self, value: str) -> int:
        self.text += value
        return len(value)

    def flush(self) -> None:
        """Satisfy the stream protocol; the buffer needs no flushing."""


class ComplianceGitPathTest(unittest.TestCase):
    """The compliance tree Git PATH keeps only absolute entries outside the repo."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = load_script("check_compliance_tree")

    def test_relative_entries_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            outside = tempfile.gettempdir()
            path_value = os.pathsep.join(("relative/bin", str(repo / "bin"), outside))
            with patch.dict(os.environ, {"PATH": path_value}):
                entries = self.tree._safe_search_path(repo).split(os.pathsep)
        self.assertNotIn(str(Path("relative/bin").resolve()), entries)
        self.assertFalse(any(entry.startswith(str(repo)) for entry in entries))
        self.assertIn(str(Path(outside).resolve()), entries)

    def test_execution_directory_uses_gettempdir(self):
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as temp_dir:
            environment = {key: value for key, value in os.environ.items() if key != "TEMP"}
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(self.tree.tempfile, "gettempdir", return_value=temp_dir):
                chosen = self.tree._safe_execution_directory(Path(repo_dir).resolve(), sys.executable)
        self.assertEqual(chosen, Path(temp_dir).resolve())


@unittest.skipIf(os.name == "nt", "the fake executable needs a POSIX execute bit")
class ConflictGitResolverTest(unittest.TestCase):
    """The conflict checker refuses a Git found inside the repository or a relative PATH."""

    def setUp(self) -> None:
        self.markers = load_script("check_conflict_markers")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        (self.repo / ".git").mkdir()
        planted = self.repo / "bin" / "git"
        planted.parent.mkdir()
        planted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        planted.chmod(planted.stat().st_mode | stat.S_IXUSR)
        self.planted = planted

    def resolved(self, trusted) -> str:
        """Resolve Git from inside the repo with a hostile PATH prefix."""
        hostile = os.pathsep.join(("bin", str(self.repo / "bin"), os.environ.get("PATH", "")))
        with patch.dict(os.environ, {"PATH": hostile}), \
                patch.object(self.markers, "_trusted_resolve_git", trusted), \
                patch.object(self.markers.Path, "cwd", return_value=self.repo):
            return self.markers._git_executable()

    def test_standalone_resolver_skips_entries(self):
        executable = self.resolved(None)
        self.assertTrue(os.path.isabs(executable))
        self.assertFalse(Path(executable).resolve().is_relative_to(self.repo))

    def test_trusted_resolver_preferred(self):
        calls = []

        def trusted(repository):
            calls.append(Path(repository))
            return "/usr/bin/git"

        self.assertEqual(self.resolved(trusted), "/usr/bin/git")
        self.assertEqual(calls, [self.repo])

    def test_missing_git_raises_runtime_error(self):
        with patch.dict(os.environ, {"PATH": str(self.repo / "bin")}), \
                patch.object(self.markers, "_trusted_resolve_git", None), \
                patch.object(self.markers.Path, "cwd", return_value=self.repo):
            with self.assertRaises(RuntimeError):
                self.markers._git_executable()

    def test_commands_use_resolved_git(self):
        with patch.object(self.markers, "_git_executable", return_value="/opt/git/bin/git"), \
                patch.object(self.markers, "_supports_no_lazy_fetch", return_value=True):
            command = self.markers._object_command(str(self.repo), "cat-file", "-t", "a" * 40)
        self.assertEqual(command[0], "/opt/git/bin/git")


if __name__ == "__main__":
    unittest.main()
