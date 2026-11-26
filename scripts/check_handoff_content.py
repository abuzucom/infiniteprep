#!/usr/bin/env python3
"""Check an untracked session handoff for unsafe content before writing it.

A handoff lives in `.scratch/<id>/HANDOFF.md` and never enters a commit, so
no CI job reads it. The session-handoff skill runs this check locally. The
file follows a fixed template: a `# Handoff` heading, then `- <field>: <value>`
lines with allowlisted fields, and indented `  - ` list items under a field.

Rejected content: unknown fields, absolute or home paths, email addresses,
and URLs outside github.com. Findings name the line number only, so a
rejected value never reaches the terminal or a log.
"""
import argparse
import re
import sys
import urllib.parse
from pathlib import Path

HEADING = "# Handoff"
FIELDS = frozenset({"branch", "commits", "prs", "paths", "status", "pending consents",
                    "verification"})
FIELD_LINE = re.compile(r"- ([a-z ]{1,32}):(?: (.{1,400}))?")
LIST_ITEM = re.compile(r"  - .{1,400}")
ABSOLUTE_PATH = re.compile(r"(?:^|[\s`(])(?:/[A-Za-z]|~/|[A-Za-z]:\\)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}\.[A-Za-z0-9.-]{1,190}")
URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,15}://[^\s`)>]{1,2000}")
ALLOWED_URL_SCHEME = "https"
# Line 1 holds the heading. Field lines start on line 2.
FIRST_BODY_LINE_NUMBER = 2
ALLOWED_URL_HOST = "github.com"
MAX_HANDOFF_BYTES = 64 * 1024
# The template defines one list. Items under any other field break the template.
LIST_FIELD = "pending consents"
FINDING_EXIT_CODE = 1


def _structure_problem(line: str, current_field: str) -> tuple:
    """Return (reason or empty string, field in effect after this line)."""
    if LIST_ITEM.fullmatch(line):
        if current_field != LIST_FIELD:
            return f"list items belong only under - {LIST_FIELD}:", current_field
        return "", current_field
    match = FIELD_LINE.fullmatch(line)
    if match is None:
        return "line is not a template field or list item", current_field
    if match.group(1) not in FIELDS:
        return "field is outside the template", current_field
    return "", match.group(1)


def _allowed_url(url: str) -> bool:
    """Return whether a URL targets github.com over https with no credentials or port."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == ALLOWED_URL_SCHEME and parts.hostname == ALLOWED_URL_HOST
            and parts.username is None and parts.password is None and port is None)


def _value_problem(line: str) -> str:
    """Return why one line holds unsafe content, or an empty string."""
    if ABSOLUTE_PATH.search(line):
        return "holds an absolute or home path; use a repository-relative path"
    if EMAIL.search(line):
        return "holds an email address"
    if any(not _allowed_url(match.group(0)) for match in URL.finditer(line)):
        return "holds a URL outside github.com"
    return ""


def content_problems(text: str) -> list:
    """Return (line number, reason) findings for one handoff text."""
    lines = text.splitlines()
    if not lines or lines[0] != HEADING:
        return [(1, f"the first line must be {HEADING}")]
    findings = []
    current_field = ""
    for number, line in enumerate(lines[1:], FIRST_BODY_LINE_NUMBER):
        if not line.strip():
            continue
        structure, current_field = _structure_problem(line, current_field)
        reason = structure or _value_problem(line)
        if reason:
            findings.append((number, reason))
    return findings


def main(argv: list) -> int:
    """Check one handoff file and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path")
    path = Path(parser.parse_args(argv).path)
    if path.is_symlink() or not path.is_file():
        print("error: the handoff must be a regular file", file=sys.stderr)
        return FINDING_EXIT_CODE
    if path.stat().st_size > MAX_HANDOFF_BYTES:
        print(f"{path.name}: exceeds the {MAX_HANDOFF_BYTES}-byte size cap", file=sys.stderr)
        return FINDING_EXIT_CODE
    text = path.read_bytes().decode("utf-8", "replace")
    findings = content_problems(text)
    for number, reason in findings:
        print(f"{path.name}:{number}: {reason}", file=sys.stderr)
    return FINDING_EXIT_CODE if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
