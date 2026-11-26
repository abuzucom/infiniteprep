#!/usr/bin/env python3
"""Enforce the dash and ASCII style rules on the given files.

A portable version of lint_style.py's checks for any path (which are
hardcoded to AGENTS.md in this repo): copy this single file into any repo
and point it at that repo's own source globs and CI. Blocking, like
lint_style.py: exits 1 on any violation, since it propagates an
already-blocking rule ("No non-ASCII characters") instead of a new one.
"""
import re
import sys
from pathlib import Path

DASH_SUBSTITUTE = re.compile(r" -{1,3} ")
EM_EN_DASH = re.compile(r"[–—]")
MAX_ASCII_CODEPOINT = 127
# A Markdown table delimiter row carries only pipes, colons, dashes and
# spaces. It is table syntax, and rewriting it to satisfy the dash rule
# changes a document to satisfy a linter instead of a reader.
TABLE_SEPARATOR = re.compile(r"^\s*\|[\s:|-]*-[\s:|-]*\|\s*$")
# A list marker opens its line. It is syntax, not a dash between clauses.
LIST_MARKER = re.compile(r"^(\s*)([-*+]|\d+\.)\s")


# prose_policy.mask_markdown_code also masks code. This file keeps its own
# stripper so it stays one portable file with no repository imports.
def strip_code(line: str, in_span: bool = False) -> tuple:
    """Return the line's prose and whether a code span is still open.

    A span can open on one line and close on the next. Pairing backticks
    within a line pairs the wrong ones and leaks the text between them.
    A preset name carrying a spaced hyphen reached the dash check that way.
    """
    prose = []
    for character in line:
        if character == "`":
            in_span = not in_span
        elif not in_span:
            prose.append(character)
    return "".join(prose), in_span


def strip_marker(line: str) -> str:
    """Remove a leading list marker. The marker is syntax and not prose."""
    return LIST_MARKER.sub("", line, count=1)


def find_violations(text: str, path: str) -> list[str]:
    """Return one message per style violation in the prose of `text`."""
    violations = []
    in_fence = False
    in_span = False
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            in_span = False
            continue
        if in_fence:
            continue
        prose, in_span = strip_code(raw, in_span)
        if EM_EN_DASH.search(prose):
            violations.append(f"{path}:{number}: em/en dash character")
        if (not TABLE_SEPARATOR.match(raw)
                and DASH_SUBSTITUTE.search(strip_marker(prose))):
            violations.append(
                f"{path}:{number}: spaced hyphen used as an em-dash substitute"
            )
        if any(ord(char) > MAX_ASCII_CODEPOINT for char in prose):
            violations.append(f"{path}:{number}: non-ASCII character in prose")
    return violations


def main() -> int:
    """Lint each given file. Return 0 when all are clean, 1 on any violation."""
    paths = sys.argv[1:]
    if not paths:
        print("usage: check_ascii.py FILE [FILE ...]", file=sys.stderr)
        return 1

    all_violations = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        all_violations.extend(find_violations(text, path))

    if all_violations:
        for message in all_violations:
            print(message, file=sys.stderr)
        print(
            "fix: rewrite the prose as separate sentences",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
