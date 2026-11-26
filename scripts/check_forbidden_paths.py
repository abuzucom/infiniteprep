#!/usr/bin/env python3
"""Fail when a commit or pull request diff touches an untracked session zone.

Session zones hold scratch files, handoffs, and logs that can carry local
paths or credentials. They stay git-ignored, but `git add -f` or a later
negation rule can still stage them. This check runs in pre-commit with
--staged and in CI with --base and --head.

Exit codes: 0 clean, 1 forbidden path found, 2 usage or git failure.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_PREFIXES = (".scratch/", ".worktrees/")
# A leading letter or digit keeps a revision from reading as a git option.
REVISION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/~^-]{0,199}")
PARENT_DIRECTORY = ".."
GIT_TIMEOUT_SECONDS = 60
FINDING_EXIT_CODE = 1
USAGE_EXIT_CODE = 2
# Repository config can name commands such as core.fsmonitor. These options
# and the minimal environment keep a read-only diff from running them.
GIT_SAFE_OPTIONS = ("-c", "core.fsmonitor=false", "-c", "core.quotePath=false")
INHERITED_ENVIRONMENT = ("PATH", "SYSTEMROOT", "HOME")


def sanitize_path(path: str) -> str:
    """Replace control characters so a filename cannot forge output lines."""
    return "".join(character if character.isprintable() else "?" for character in path)


def forbidden_paths(paths: list) -> list:
    """Return every path that sits below a forbidden zone prefix."""
    return [path for path in paths if path.startswith(FORBIDDEN_PREFIXES)]


def _git_environment() -> dict:
    """Return the allowlisted environment for read-only git calls."""
    environment = {name: os.environ[name] for name in INHERITED_ENVIRONMENT if name in os.environ}
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["LC_ALL"] = "C"
    return environment


def _diff_paths(root: Path, revisions: list) -> list:
    """Return NUL-separated paths from one git diff, or raise OSError."""
    command = ["git", "-C", str(root), *GIT_SAFE_OPTIONS, "diff", "-z", "--name-only",
               *revisions, "--"]
    completed = subprocess.run(command, capture_output=True, timeout=GIT_TIMEOUT_SECONDS,
                               env=_git_environment(), check=False)
    if completed.returncode != 0:
        raise OSError("git diff failed")
    return [path for path in completed.stdout.decode("utf-8", "replace").split("\0") if path]


def _valid_revision(value: str) -> bool:
    """Return whether a value names one revision and no option or range."""
    return bool(REVISION_PATTERN.fullmatch(value)) and PARENT_DIRECTORY not in value


def _parse(argv: list) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--base")
    parser.add_argument("--head")
    return parser.parse_args(argv)


def _revisions(options: argparse.Namespace) -> list | None:
    """Return the diff revision arguments, or None when they are unsafe."""
    if options.staged:
        return ["--cached"]
    if options.head is None or not all(map(_valid_revision, (options.base, options.head))):
        return None
    return [f"{options.base}...{options.head}"]


def main(argv: list) -> int:
    """Check one diff and return a process exit code."""
    options = _parse(argv)
    revisions = _revisions(options)
    if revisions is None:
        print("error: --base and --head must each name one revision", file=sys.stderr)
        return USAGE_EXIT_CODE
    try:
        found = forbidden_paths(_diff_paths(Path(options.root), revisions))
    except (OSError, subprocess.TimeoutExpired):
        print("error: git diff failed; check the revisions and the repository root",
              file=sys.stderr)
        return USAGE_EXIT_CODE
    for path in found:
        print(f"{sanitize_path(path)}: session zone paths never belong in a commit. "
              "Unstage the file with git restore --staged.", file=sys.stderr)
    return FINDING_EXIT_CODE if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
