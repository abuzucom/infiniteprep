"""Behavioral tests for the guard paths of the one-click scripts under scripts/dev."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_SCRIPTS = REPO_ROOT / "scripts" / "dev"
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")
GIT = shutil.which("git")
GUARD_EXIT = 2

needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed on this runner")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed on this runner")


def git(repo: Path, *args: str) -> None:
    """Run one git command in repo with a fixed local identity."""
    assert GIT is not None
    subprocess.run(
        [GIT, "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def make_repo(tmp_path: Path, branch: str = "main") -> Path:
    """Create a git repo holding copies of the dev scripts, committed on branch."""
    repo = tmp_path / "repo"
    shutil.copytree(DEV_SCRIPTS, repo / "scripts" / "dev")
    git(repo, "init", "-q", "-b", branch)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "scripts")
    return repo


def run_bash(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a shell script with bash and capture its output as text.

    Forward-slash paths keep dirname working under Git Bash on Windows.
    """
    assert BASH is not None
    command = [BASH, script.as_posix(), *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


@needs_bash
def test_update_script_refuses_a_dirty_tree(tmp_path: Path) -> None:
    """Uncommitted changes stop the run before anything is pulled or installed."""
    repo = make_repo(tmp_path)
    (repo / "notes.txt").write_text("local edit", encoding="utf-8")
    result = run_bash(repo / "scripts" / "dev" / "update_and_check.sh", "--dry-run")
    assert result.returncode == GUARD_EXIT
    assert "uncommitted changes" in result.stdout


@needs_bash
def test_update_script_refuses_a_branch_other_than_main(tmp_path: Path) -> None:
    """A checkout on another branch stops the run unless --no-pull is given."""
    repo = make_repo(tmp_path, branch="feature")
    script = repo / "scripts" / "dev" / "update_and_check.sh"
    refused = run_bash(script, "--dry-run")
    assert refused.returncode == GUARD_EXIT
    assert "not main" in refused.stdout
    allowed = run_bash(script, "--dry-run", "--no-pull")
    assert allowed.returncode == 0
    assert "git pull" not in allowed.stdout


@needs_bash
def test_update_script_dry_run_lists_the_steps_and_changes_nothing(tmp_path: Path) -> None:
    """A dry run on a clean main prints every step and leaves the checkout untouched."""
    repo = make_repo(tmp_path)
    result = run_bash(repo / "scripts" / "dev" / "update_and_check.sh", "--dry-run")
    assert result.returncode == 0
    for step in ("git pull --ff-only", "Would install", "Would run checks", "Would launch"):
        assert step in result.stdout
    assert not (repo / "logs").exists()
    assert not (repo / ".venv").exists()


@needs_bash
def test_update_script_rejects_unknown_options(tmp_path: Path) -> None:
    """An unknown option stops the run with the list of valid options."""
    repo = make_repo(tmp_path)
    result = run_bash(repo / "scripts" / "dev" / "update_and_check.sh", "--force")
    assert result.returncode == GUARD_EXIT
    assert "Unknown option: --force" in result.stdout


@needs_bash
def test_copy_script_copies_at_most_per_format_and_leaves_sources(tmp_path: Path) -> None:
    """The copy helper takes at most PER_FORMAT files of each format and never edits sources."""
    repo = make_repo(tmp_path)
    source = tmp_path / "music"
    source.mkdir()
    for index in range(3):
        (source / f"track{index}.mp3").write_bytes(b"mp3 bytes")
    (source / "album.FLAC").write_bytes(b"flac bytes")
    (source / "notes.txt").write_text("not music", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    result = run_bash(repo / "scripts" / "dev" / "make_test_copies.sh", source.as_posix(), "2")
    assert result.returncode == 0, result.stdout
    (dest,) = (repo / "scratch" / "test-library").iterdir()
    copied = sorted(path.name for path in dest.iterdir())
    assert copied == ["album.FLAC", "track0.mp3", "track1.mp3"]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before


@needs_bash
def test_copy_script_rejects_a_missing_folder(tmp_path: Path) -> None:
    """A source folder that does not exist stops the helper before it creates anything."""
    repo = make_repo(tmp_path)
    result = run_bash(repo / "scripts" / "dev" / "make_test_copies.sh", str(tmp_path / "nope"))
    assert result.returncode == GUARD_EXIT
    assert not (repo / "scratch").exists()


@needs_bash
def test_copy_script_treats_a_dash_named_folder_as_a_path(tmp_path: Path) -> None:
    """A source folder named like a find option is copied from, never run as an option."""
    repo = make_repo(tmp_path)
    work = tmp_path / "work"
    (work / "-delete").mkdir(parents=True)
    (work / "-delete" / "track.mp3").write_bytes(b"mp3 bytes")
    keep = work / "keep.txt"
    keep.write_text("must survive", encoding="utf-8")
    script = (repo / "scripts" / "dev" / "make_test_copies.sh").as_posix()
    assert BASH is not None
    result = subprocess.run(
        [BASH, script, "-delete"], cwd=work, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert keep.read_text(encoding="utf-8") == "must survive"
    assert (work / "-delete" / "track.mp3").exists()
    (dest,) = (repo / "scratch" / "test-library").iterdir()
    assert [path.name for path in dest.iterdir()] == ["track.mp3"]


@needs_bash
@pytest.mark.parametrize("per_format", ["0", "1001", "x"])
def test_copy_script_rejects_a_count_outside_one_to_a_thousand(
    tmp_path: Path, per_format: str
) -> None:
    """PER_FORMAT outside 1 to 1000 stops the helper before it creates anything."""
    repo = make_repo(tmp_path)
    source = tmp_path / "music"
    source.mkdir()
    script = repo / "scripts" / "dev" / "make_test_copies.sh"
    result = run_bash(script, source.as_posix(), per_format)
    assert result.returncode == GUARD_EXIT
    assert "from 1 to 1000" in result.stdout
    assert not (repo / "scratch").exists()


@needs_pwsh
@pytest.mark.parametrize("script", ["update_and_check.ps1", "make_test_copies.ps1"])
def test_powershell_scripts_parse_without_errors(script: str) -> None:
    """Each PowerShell script parses with no syntax errors."""
    assert PWSH is not None
    command = (
        "$errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{DEV_SCRIPTS / script}', [ref]$null, [ref]$errors) | Out-Null; "
        "exit $errors.Count"
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
