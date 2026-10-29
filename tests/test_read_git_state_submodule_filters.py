#!/usr/bin/env python3
"""Tests that the status query never runs a submodule's filter drivers."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import read_git_state

TRACKED_FILE = "data.txt"
SUBMODULE_PATH = "child"
SUBMODULE_DRIVER = "childprobe"


def _git(repository: Path, *arguments: str) -> str:
    """Run one fixture Git command and fail loudly on error."""
    result = subprocess.run(["git", "-C", str(repository), *arguments],
                            check=True, capture_output=True, text=True)
    return result.stdout


def _commit_tracked_file(repository: Path) -> None:
    """Create a repository with one committed tracked file."""
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    (repository / TRACKED_FILE).write_text("one\n", encoding="utf-8")
    _git(repository, "add", TRACKED_FILE)
    _git(repository, "commit", "--quiet", "--message", "fixture")


def _build_superproject(root: Path) -> tuple[Path, Path]:
    """Return a superproject whose dirty submodule maps to a marker filter."""
    source = root / "source"
    _commit_tracked_file(source)
    superproject = root / "superproject"
    _commit_tracked_file(superproject)
    _git(superproject, "-c", "protocol.file.allow=always", "submodule",
         "add", "--quiet", str(source), SUBMODULE_PATH)
    _git(superproject, "commit", "--quiet", "--message", "add submodule")
    marker = root / "filter-ran"
    helper = root / "clean_filter.py"
    helper.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
        "sys.stdout.write(sys.stdin.read())\n",
        encoding="utf-8",
    )
    child = superproject / SUBMODULE_PATH
    command = f'"{sys.executable}" "{helper}"'
    _git(child, "config", f"filter.{SUBMODULE_DRIVER}.clean", command)
    _git(child, "config", f"filter.{SUBMODULE_DRIVER}.required", "true")
    info = Path(_git(child, "rev-parse", "--absolute-git-dir").strip()) / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "attributes").write_text(
        f"*.txt filter={SUBMODULE_DRIVER}\n", encoding="utf-8")
    (child / TRACKED_FILE).write_text("two\n", encoding="utf-8")
    return superproject, marker


class SubmoduleFilterDriverTest(unittest.TestCase):
    """read_state("status") must not run filters configured in a submodule."""

    def test_submodule_filter_never_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            superproject, marker = _build_superproject(Path(temporary))
            read_git_state.read_state("status", superproject)
            self.assertFalse(marker.exists(), "status ran a submodule filter")


if __name__ == "__main__":
    unittest.main()
