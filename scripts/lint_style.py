#!/usr/bin/env python3
"""Enforce AGENTS.md dash, ASCII, and line length rules through the portable checker."""
import sys
from pathlib import Path

try:
    from scripts.check_ascii import find_violations as find_path_violations
except ModuleNotFoundError:
    from check_ascii import find_violations as find_path_violations

SOURCE = "AGENTS.md"
# The Code quality line length rule caps every line at 120 characters.
MAX_LINE_LENGTH = 120


def find_violations(text: str) -> list[str]:
    """Return portable checker findings under the canonical source path."""
    return find_path_violations(text, SOURCE)


def find_line_violations(text: str) -> list[str]:
    """Return one finding per canonical source line above the length limit."""
    return [
        f"{SOURCE}:{number}: line has {len(line)} characters, limit {MAX_LINE_LENGTH}"
        for number, line in enumerate(text.split("\n"), start=1)
        if len(line) > MAX_LINE_LENGTH
    ]


def _print_findings(violations: list[str], fix: str) -> None:
    """Print each finding and one recovery line."""
    for message in violations:
        print(message, file=sys.stderr)
    print(fix, file=sys.stderr)


def lint() -> int:
    """Lint the canonical source document."""
    root = Path(__file__).resolve().parent.parent
    source = root / SOURCE
    if not source.is_file():
        print(f"error: {SOURCE} not found at {root}", file=sys.stderr)
        return 1
    text = source.read_text(encoding="utf-8")
    violations = find_violations(text)
    if violations:
        _print_findings(violations, "fix: rewrite the prose as separate sentences")
    line_violations = find_line_violations(text)
    if line_violations:
        _print_findings(line_violations, "fix: wrap the line at a space with identical words")
    if violations or line_violations:
        return 1
    print("style clean")
    return 0


if __name__ == "__main__":
    sys.exit(lint())
