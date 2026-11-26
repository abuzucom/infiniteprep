"""Behavioral tests for the guard paths of the one-click scripts under scripts/dev."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_SCRIPTS = REPO_ROOT / "scripts" / "dev"
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")
GIT = shutil.which("git")
GUARD_EXIT = 2
TEMPLATE_BRANCHES = ("main", "feature")


def bash_can_run_scripts() -> bool:
    """Return whether the runner's bash can run a script by its path.

    Git Bash on Windows runs a forward-slash Windows path. WSL bash needs a
    POSIX path and reports every Windows path as missing. The scripts under
    test run from a temporary directory, so the probe runs from one too.
    """
    if BASH is None:
        return False
    with tempfile.TemporaryDirectory() as probe_dir:
        probe = Path(probe_dir) / "probe.sh"
        probe.write_text("exit 0\n", encoding="utf-8")
        result = subprocess.run(
            [BASH, probe.as_posix()], capture_output=True, text=True, check=False
        )
    return result.returncode == 0


needs_bash = pytest.mark.skipif(
    not bash_can_run_scripts(),
    reason="bash cannot run a script by its path on this runner",
)
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


def build_template_repo(repo: Path, branch: str) -> Path:
    """Create a git repo at repo holding copies of the dev scripts, committed on branch."""
    shutil.copytree(DEV_SCRIPTS, repo / "scripts" / "dev")
    git(repo, "init", "-q", "-b", branch)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "scripts")
    return repo


@pytest.fixture(scope="module")
def repo_templates(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build one committed template repo per branch, once for the module.

    Each git spawn costs tens of milliseconds, and every test needs the same commit.
    """
    root = tmp_path_factory.mktemp("dev-script-templates")
    return {branch: build_template_repo(root / branch, branch) for branch in TEMPLATE_BRANCHES}


def make_repo(tmp_path: Path, templates: dict[str, Path], branch: str = "main") -> Path:
    """Copy the template repo for branch into tmp_path. The copy keeps its commit."""
    repo = tmp_path / "repo"
    shutil.copytree(templates[branch], repo)
    return repo


def run_bash(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a shell script with bash and capture its output as text.

    Forward-slash paths keep dirname working under Git Bash on Windows.
    """
    # Only needs_bash tests call this helper, and that marker skips without bash.
    command = [cast(str, BASH), script.as_posix(), *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


@needs_bash
def test_update_script_refuses_dirty_tree(tmp_path: Path, repo_templates: dict[str, Path]) -> None:
    """Uncommitted changes stop the run before anything is pulled or installed."""
    repo = make_repo(tmp_path, repo_templates)
    (repo / "notes.txt").write_text("local edit", encoding="utf-8")
    result = run_bash(repo / "scripts" / "dev" / "update_and_check.sh", "--dry-run")
    assert result.returncode == GUARD_EXIT
    assert "uncommitted changes" in result.stdout


@needs_bash
def test_update_script_requires_main(tmp_path: Path, repo_templates: dict[str, Path]) -> None:
    """A checkout on another branch stops the run unless --no-pull is given."""
    repo = make_repo(tmp_path, repo_templates, branch="feature")
    script = repo / "scripts" / "dev" / "update_and_check.sh"
    refused = run_bash(script, "--dry-run")
    assert refused.returncode == GUARD_EXIT
    assert "not main" in refused.stdout
    allowed = run_bash(script, "--dry-run", "--no-pull")
    assert allowed.returncode == 0
    assert "git pull" not in allowed.stdout


@needs_bash
def test_update_dry_run_changes_nothing(tmp_path: Path, repo_templates: dict[str, Path]) -> None:
    """A dry run on a clean main prints every step and leaves the checkout untouched."""
    repo = make_repo(tmp_path, repo_templates)
    result = run_bash(repo / "scripts" / "dev" / "update_and_check.sh", "--dry-run")
    assert result.returncode == 0
    for step in ("git pull --ff-only", "Would install", "Would run checks", "Would launch"):
        assert step in result.stdout
    assert not (repo / "logs").exists()
    assert not (repo / ".venv").exists()


@needs_bash
def test_update_script_rejects_unknown_options(
    tmp_path: Path, repo_templates: dict[str, Path]
) -> None:
    """An unknown option stops the run with the list of valid options."""
    repo = make_repo(tmp_path, repo_templates)
    result = run_bash(repo / "scripts" / "dev" / "update_and_check.sh", "--force")
    assert result.returncode == GUARD_EXIT
    assert "Unknown option: --force" in result.stdout


@needs_bash
def test_copy_caps_format_keeps_sources(tmp_path: Path, repo_templates: dict[str, Path]) -> None:
    """The copy helper takes at most PER_FORMAT files of each format and never edits sources."""
    repo = make_repo(tmp_path, repo_templates)
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
def test_copy_script_rejects_missing_folder(
    tmp_path: Path, repo_templates: dict[str, Path]
) -> None:
    """A source folder that does not exist stops the helper before it creates anything."""
    repo = make_repo(tmp_path, repo_templates)
    result = run_bash(repo / "scripts" / "dev" / "make_test_copies.sh", str(tmp_path / "nope"))
    assert result.returncode == GUARD_EXIT
    assert not (repo / "scratch").exists()


@needs_bash
def test_copy_takes_dash_folder_path(tmp_path: Path, repo_templates: dict[str, Path]) -> None:
    """A source folder named like a find option is copied from, never run as an option."""
    repo = make_repo(tmp_path, repo_templates)
    work = tmp_path / "work"
    (work / "-delete").mkdir(parents=True)
    (work / "-delete" / "track.mp3").write_bytes(b"mp3 bytes")
    keep = work / "keep.txt"
    keep.write_text("must survive", encoding="utf-8")
    script = (repo / "scripts" / "dev" / "make_test_copies.sh").as_posix()
    # The needs_bash marker skips this test when bash is absent.
    result = subprocess.run(
        [cast(str, BASH), script, "-delete"], cwd=work, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert keep.read_text(encoding="utf-8") == "must survive"
    assert (work / "-delete" / "track.mp3").exists()
    (dest,) = (repo / "scratch" / "test-library").iterdir()
    assert [path.name for path in dest.iterdir()] == ["track.mp3"]


@needs_bash
@pytest.mark.parametrize("per_format", ["0", "1001", "x"])
def test_copy_rejects_count_beyond_range(
    tmp_path: Path, repo_templates: dict[str, Path], per_format: str
) -> None:
    """PER_FORMAT outside 1 to 1000 stops the helper before it creates anything."""
    repo = make_repo(tmp_path, repo_templates)
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
    # The needs_pwsh marker skips this test when pwsh is absent.
    pwsh_path = cast(str, PWSH)
    command = (
        "$errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{DEV_SCRIPTS / script}', [ref]$null, [ref]$errors) | Out-Null; "
        "exit $errors.Count"
    )
    result = subprocess.run(
        [pwsh_path, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
