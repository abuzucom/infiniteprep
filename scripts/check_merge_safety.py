#!/usr/bin/env python3
"""Verify preconditions before any merge action."""
import argparse
import subprocess
import sys
from pathlib import Path

BRANCH_CHECKER = "check_branch_name.py"
CONFLICT_CHECKER = "check_conflict_markers.py"
GATE_CHECKER = "check_gate_adoption.py"
SYNC_SCRIPT = "sync.py"
TEST_RUNNER = "run_tests.py"
CHECKER_TIMEOUT_SECONDS = 120
TEST_TIMEOUT_SECONDS = 1800
MAX_DIAGNOSTIC_LENGTH = 400
OPERATION_MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
REBASE_DIRECTORIES = ("rebase-merge", "rebase-apply")


def _truncate(text: str) -> str:
    """Return one bounded diagnostic string."""
    flattened = " ".join(text.split())
    if len(flattened) > MAX_DIAGNOSTIC_LENGTH:
        return flattened[:MAX_DIAGNOSTIC_LENGTH]
    return flattened


def _run(command: list[str], root: Path, timeout: int) -> tuple[int, str]:
    """Run one process and return its exit code and bounded diagnostic."""
    try:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"{command[0]} failed: {error}"
    if result.returncode == 0:
        return 0, ""
    diagnostic = result.stderr.strip() or result.stdout.strip()
    return result.returncode, _truncate(diagnostic or "reported a violation")


def _git_output(root: Path, *args: str) -> tuple[int, str]:
    """Run one Git command and return its exit code and standard output."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=CHECKER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"git failed: {error}"
    return result.returncode, result.stdout


def _git_directory(root: Path) -> Path | None:
    """Return the Git administration directory for one repository root."""
    code, output = _git_output(root, "rev-parse", "--absolute-git-dir")
    if code != 0:
        return None
    candidate = output.strip()
    if not candidate:
        return None
    return Path(candidate)


def _current_branch(root: Path) -> str:
    """Return the checked-out branch name from Git metadata."""
    git_dir = _git_directory(root)
    if git_dir is None:
        return ""
    head = git_dir / "HEAD"
    try:
        content = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    prefix = "ref: refs/heads/"
    if content.startswith(prefix):
        return content[len(prefix):]
    return ""


def _operation_errors(root: Path) -> list[str]:
    """Return every active Git operation that blocks a merge."""
    git_dir = _git_directory(root)
    if git_dir is None:
        return ["the target directory is not a Git repository"]
    errors = []
    for marker in OPERATION_MARKERS:
        if (git_dir / marker).exists():
            errors.append(f"a Git operation is in progress: {marker}")
    for name in REBASE_DIRECTORIES:
        if (git_dir / name).is_dir():
            errors.append(f"a Git rebase is in progress: {name}")
    return errors


def _worktree_is_clean(root: Path) -> tuple[bool, str]:
    """Return whether the worktree holds no tracked change."""
    code, output = _git_output(root, "status", "--porcelain", "--untracked-files=no")
    if code != 0:
        return False, "git status failed"
    entries = [line for line in output.splitlines() if line.strip()]
    if not entries:
        return True, ""
    changed = [entry[3:].strip().strip('"') for entry in entries[:10]]
    return False, ", ".join(changed)


def _conflict_markers(root: Path, scripts: Path) -> tuple[bool, str]:
    """Return whether the repository holds unresolved conflict markers."""
    checker = scripts / CONFLICT_CHECKER
    code, diagnostic = _run(
        [sys.executable, str(checker), "--all"], root, CHECKER_TIMEOUT_SECONDS
    )
    return code == 0, diagnostic


def _branch_preflight(root: Path, scripts: Path) -> tuple[bool, str]:
    """Return whether the checked-out branch passes strict agent preflight."""
    checker = scripts / BRANCH_CHECKER
    command = [
        sys.executable,
        str(checker),
        "--strict-agent-preflight",
        "--repo",
        str(root),
    ]
    # The branch checker prefers the CI pull request head name over the
    # repository it inspects. An explicit name binds the verdict to the
    # branch that the merge would run on.
    branch = _current_branch(root)
    if branch:
        command.append(branch)
    code, diagnostic = _run(command, root, CHECKER_TIMEOUT_SECONDS)
    return code == 0, diagnostic


def _gate_adoption(root: Path, scripts: Path) -> tuple[bool, str]:
    """Return whether every adopted client carries the complete gate set."""
    checker = scripts / GATE_CHECKER
    code, diagnostic = _run(
        [sys.executable, str(checker), "--root", str(root)],
        root,
        CHECKER_TIMEOUT_SECONDS,
    )
    return code == 0, diagnostic


def _policy_sync(root: Path, scripts: Path) -> tuple[bool, str]:
    """Return whether every policy copy matches the canonical source."""
    script = scripts / SYNC_SCRIPT
    code, diagnostic = _run(
        [sys.executable, str(script), "--check"], root, CHECKER_TIMEOUT_SECONDS
    )
    return code == 0, diagnostic


def _test_suite(root: Path, scripts: Path) -> tuple[bool, str]:
    """Return whether the full test suite passes."""
    runner = scripts / TEST_RUNNER
    code, diagnostic = _run(
        [sys.executable, str(runner)], root, TEST_TIMEOUT_SECONDS
    )
    return code == 0, diagnostic


def verify_merge_safety(
    target_ref: str,
    repo_root: Path | None = None,
    scripts_dir: Path | None = None,
    run_tests: bool = False,
) -> tuple[int, list[str]]:
    """Return the merge verdict and every precondition that failed."""
    root = Path(repo_root or Path.cwd()).resolve()
    scripts = Path(scripts_dir).resolve() if scripts_dir else Path(__file__).resolve().parent
    target = (target_ref or "").strip()
    if not target:
        return 1, ["target reference is empty"]
    branch = _current_branch(root)
    if branch and target == branch:
        return 1, [f"cannot merge branch into itself: {target}"]
    errors = _operation_errors(root)
    if errors:
        return 1, errors
    clean, status = _worktree_is_clean(root)
    if not clean:
        errors.append(f"worktree is not clean: {status}")
    passed, diagnostic = _conflict_markers(root, scripts)
    if not passed:
        errors.append(f"conflict markers present: {diagnostic}")
    passed, diagnostic = _branch_preflight(root, scripts)
    if not passed:
        errors.append(f"branch preflight failed: {diagnostic}")
    passed, diagnostic = _gate_adoption(root, scripts)
    if not passed:
        errors.append(f"gate adoption incomplete: {diagnostic}")
    passed, diagnostic = _policy_sync(root, scripts)
    if not passed:
        errors.append(f"policy copies are not synchronized: {diagnostic}")
    if run_tests:
        passed, diagnostic = _test_suite(root, scripts)
        if not passed:
            errors.append(f"test suite failed: {diagnostic}")
    return (1 if errors else 0), errors


def main(argv: list[str] | None = None) -> int:
    """Verify one merge target and report every failed precondition."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="target branch or reference to merge")
    parser.add_argument("--root", default=".", help="repository root to inspect")
    parser.add_argument(
        "--run-tests", action="store_true", help="run the full test suite first"
    )
    args = parser.parse_args(argv)
    code, errors = verify_merge_safety(
        args.target, Path(args.root), run_tests=args.run_tests
    )
    if code != 0:
        for error in errors:
            print(f"pre-merge safety check failed: {error}", file=sys.stderr)
        return code
    print(f"pre-merge safety check passed for target: {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
