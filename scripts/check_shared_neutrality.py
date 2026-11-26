#!/usr/bin/env python3
"""Fail when a shared skill or adoptable policy file names a specific owner.

Shared files reach every adopting organization byte for byte. A named owner
or repository there points adopters at somebody else's project. Source
repository facts belong between the source orientation markers, which
adoption strips, so this check skips those blocks.

The check reads two forms:
- a bare `owner/repository` token in any text, code span, link, or list
- a `github.com/<owner>/` URL

A token counts only when no letter, digit, `.`, `/`, or `-` precedes it.
Nested paths such as `skills/name/SKILL.md` therefore match only their
first segment.

A first segment that names a top-level entry of this repository, a branch
type, a placeholder, or the public `actions` owner passes.
"""
import argparse
import re
import sys
from pathlib import Path

SKILL_GLOB = "skills/*/SKILL.md"
POLICY_FILES = (
    "AGENTS.md",
    "docs/agent-policy/adoption.md",
    "docs/agent-policy/enforcement.md",
    "docs/agent-policy/clients.md",
    "docs/agent-policy/github.md",
    "docs/agent-policy/security.md",
    "docs/enforcement-matrix.md",
)
SOURCE_START = "<!-- source orientation:start -->"
SOURCE_END = "<!-- source orientation:end -->"
BARE_OWNER = re.compile(r"(?<![A-Za-z0-9./-])([A-Za-z0-9][A-Za-z0-9-]{0,38})/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
GITHUB_URL_OWNER = re.compile(r"github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/")
BRANCH_TYPES = frozenset({"feat", "fix", "chore", "docs", "test", "ci", "release", "hotfix",
                          "claude"})
# `actions/checkout` and similar name public GitHub actions, not an adopter.
PUBLIC_OWNERS = frozenset({"actions"})
PLACEHOLDER_OWNERS = frozenset({"owner"})
# Prose such as "MD5/SHA-1" pairs algorithm names, not an owner and repository.
ALGORITHM_PAIRS = frozenset({"md5"})
REPOSITORY_ENTRIES = frozenset({
    "hooks", "scripts", "skills", "docs", "tests", "plan", "adopters", "origin", "refs",
})
FINDING_EXIT_CODE = 1
MAX_FILE_BYTES = 256 * 1024


def _allowed_owner(owner: str) -> bool:
    """Return whether a first segment names no specific organization."""
    folded = owner.casefold()
    return (folded in BRANCH_TYPES or folded in PUBLIC_OWNERS or folded in PLACEHOLDER_OWNERS
            or folded in REPOSITORY_ENTRIES or folded in ALGORITHM_PAIRS)


def _line_references(line: str) -> list:
    """Return the owner references on one line."""
    found = [match.group(0).rstrip(".") for match in BARE_OWNER.finditer(line)
             if not _allowed_owner(match.group(1))]
    found += [match.group(0) for match in GITHUB_URL_OWNER.finditer(line)
              if not _allowed_owner(match.group(1))]
    return found


def _marker_scan(lines: list) -> tuple:
    """Return (line numbers inside matched marker pairs, marker problems)."""
    skipped = set()
    problems = []
    open_line = None
    for number, line in enumerate(lines, 1):
        if SOURCE_START in line:
            if open_line is not None:
                problems.append((number, "nested source orientation start marker"))
                continue
            open_line = number
        elif SOURCE_END in line:
            if open_line is None:
                problems.append((number, "source orientation end marker without a start"))
                continue
            skipped.update(range(open_line, number + 1))
            open_line = None
    if open_line is not None:
        problems.append((open_line, "source orientation start marker without an end"))
    return skipped, problems


def marker_problems(text: str) -> list:
    """Return (line number, reason) for every unmatched or nested marker."""
    return _marker_scan(text.splitlines())[1]


def owner_references(text: str) -> list:
    """Return (line number, reference) pairs outside matched source orientation blocks."""
    lines = text.splitlines()
    skipped = _marker_scan(lines)[0]
    return [(number, reference) for number, line in enumerate(lines, 1)
            if number not in skipped for reference in _line_references(line)]


def shared_files(root: Path) -> list:
    """Return every present shared skill and adoptable policy file."""
    candidates = sorted(root.glob(SKILL_GLOB)) + [root / name for name in POLICY_FILES]
    return [path for path in candidates if path.is_file() and not path.is_symlink()]


def main(argv: list) -> int:
    """Scan the shared files of one root and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    root = Path(parser.parse_args(argv).root)
    failed = False
    for path in shared_files(root):
        relative = path.relative_to(root).as_posix()
        if path.stat().st_size > MAX_FILE_BYTES:
            failed = True
            print(f"{relative}: exceeds the {MAX_FILE_BYTES}-byte size cap", file=sys.stderr)
            continue
        text = path.read_bytes().decode("utf-8", "replace")
        for number, reason in marker_problems(text):
            failed = True
            print(f"{relative}:{number}: {reason}", file=sys.stderr)
        for number, reference in owner_references(text):
            failed = True
            print(f"{relative}:{number}: names `{reference}`. Use `<owner>/<repo>` or move "
                  "the fact between the source orientation markers.", file=sys.stderr)
    return FINDING_EXIT_CODE if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
