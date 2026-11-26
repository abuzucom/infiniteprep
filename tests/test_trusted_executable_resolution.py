#!/usr/bin/env python3
"""Characterize trusted executable resolution across the three resolvers.

scripts/trusted_gh.py, scripts/trusted_git.py, and the standalone copy in
scripts/check_git_identity.py must pick the same executable from the same
PATH and fail with their documented messages.
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
EXECUTABLE_FILE_MODE = 0o755
PLAIN_FILE_MODE = 0o644
IS_WINDOWS = os.name == "nt"


def load_script(name: str):
    """Import one script module from its repository path."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trusted_gh = load_script("trusted_gh")
trusted_git = load_script("trusted_git")
identity = load_script("check_git_identity")


def tool_name(tool: str) -> str:
    """Return the platform executable file name for one tool."""
    return f"{tool}.exe" if IS_WINDOWS else tool


def write_tool(directory: Path, tool: str, mode: int) -> Path:
    """Create one fake executable file with a file mode."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / tool_name(tool)
    path.write_text("fake tool\n", encoding="utf-8")
    path.chmod(mode)
    return path


def build_path(root: Path, tool: str) -> tuple[Path, str, Path]:
    """Build a repository, a PATH of rejected entries, and one valid entry.

    The PATH lists, in order: a repository directory, a relative entry, a
    symlink (POSIX only), a non-executable file (POSIX only), and the valid
    executable. Windows skips the symlink and mode entries because the
    resolvers do not apply the X_OK test there and symlinks need privileges.
    """
    repository = root / "repo"
    write_tool(repository / "bin", tool, EXECUTABLE_FILE_MODE)
    entries = [str(repository / "bin"), "relative-bin"]
    valid = write_tool(root / "valid", tool, EXECUTABLE_FILE_MODE)
    if not IS_WINDOWS:
        link_directory = root / "link"
        link_directory.mkdir()
        (link_directory / tool_name(tool)).symlink_to(valid)
        write_tool(root / "plain", tool, PLAIN_FILE_MODE)
        entries += [str(link_directory), str(root / "plain")]
    entries.append(str(root / "valid"))
    return repository, os.pathsep.join(entries), valid


class ExecutableSelectionTest(unittest.TestCase):
    """Each resolver skips rejected PATH entries and picks the valid one."""

    def assert_selects_valid(self, tool: str, resolver) -> None:
        """Run one resolver against the rejected-then-valid PATH."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, search_path, valid = build_path(root, tool)
            with patch.dict(os.environ, {"PATH": search_path}):
                self.assertEqual(resolver(repository), str(valid.resolve()))

    def test_github_cli_resolver(self):
        self.assert_selects_valid("gh", trusted_gh.resolve_gh)

    def test_git_resolver(self):
        self.assert_selects_valid("git", trusted_git.resolve_git)

    def test_standalone_git_resolver(self):
        self.assert_selects_valid("git", identity._standalone_resolve_git)


class MissingExecutableTest(unittest.TestCase):
    """Each resolver keeps its documented failure message."""

    def assert_missing(self, tool: str, resolver, message: str) -> None:
        """Run one resolver with only a repository PATH entry."""
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary).resolve() / "repo"
            write_tool(repository / "bin", tool, EXECUTABLE_FILE_MODE)
            with patch.dict(os.environ, {"PATH": str(repository / "bin")}):
                with self.assertRaises(FileNotFoundError) as caught:
                    resolver(repository)
        self.assertEqual(str(caught.exception), message)

    def test_github_cli_message(self):
        self.assert_missing("gh", trusted_gh.resolve_gh,
                            "trusted GitHub CLI executable was not found on PATH")

    def test_git_message(self):
        self.assert_missing("git", trusted_git.resolve_git,
                            "trusted Git executable was not found on PATH")

    def test_standalone_git_message(self):
        self.assert_missing("git", identity._standalone_resolve_git,
                            "trusted Git executable was not found on PATH")


class SafeDirectoryTest(unittest.TestCase):
    """GitHub CLI also tries the working directory; Git does not."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name).resolve()
        self.repository = root / "repo"
        self.repository.mkdir()
        self.outside_cwd = root / "cwd"
        self.outside_cwd.mkdir()
        self.inside_executable = self.repository / "bin" / tool_name("tool")
        self.outside_executable = write_tool(root / "tools", "tool",
                                             EXECUTABLE_FILE_MODE)
        self.original_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.temporary.cleanup()

    def select(self, module, executable: Path, cwd: Path) -> Path:
        """Run one module's selector with an in-repository temp directory."""
        os.chdir(cwd)
        with patch.object(module.tempfile, "gettempdir",
                          return_value=str(self.repository)):
            return module._safe_directory(self.repository, executable)

    def test_github_cli_directory_order(self):
        selected = self.select(trusted_gh, self.outside_executable, self.outside_cwd)
        self.assertEqual(selected, self.outside_cwd)

    def test_git_skips_working_directory(self):
        selected = self.select(trusted_git, self.outside_executable, self.outside_cwd)
        self.assertEqual(selected, self.outside_executable.parent)

    def test_github_cli_missing_safe_directory(self):
        with self.assertRaises(OSError) as caught:
            self.select(trusted_gh, self.inside_executable, self.repository)
        self.assertEqual(
            str(caught.exception),
            "no safe external directory is available for GitHub CLI execution")

    def test_git_message_without_safe_directory(self):
        with self.assertRaises(OSError) as caught:
            self.select(trusted_git, self.inside_executable, self.repository)
        self.assertEqual(
            str(caught.exception),
            "no safe external directory is available for Git execution")


if __name__ == "__main__":
    unittest.main()
