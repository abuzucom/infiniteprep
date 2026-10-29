#!/usr/bin/env python3
"""Block malformed or unversioned changelogs."""
import argparse
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

try:
    from scripts.trusted_git import run_git
except ModuleNotFoundError:
    from trusted_git import run_git

VERSION_PATTERN = re.compile(r"^## \[(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?\] \((\d{4}-\d{2}-\d{2})\)$")
HEADING_PATTERN = re.compile(r"^## \[.+\](?: .*)?$")
UNRELEASED_PATTERN = re.compile(r"^## \[Unreleased\]$")
REVISION_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{7,40}|[A-Za-z0-9_./~^\-]+)$")


class ReleaseHeading(NamedTuple):
    """One versioned heading; release_date is None when the date is invalid."""
    line_number: int
    version_key: tuple
    release_date: date | None


def utc_date(aware_moment: datetime) -> date:
    """Return the UTC calendar date of an aware datetime."""
    if aware_moment.utcoffset() is None:
        raise ValueError("utc_date needs a timezone-aware datetime; "
                         "pass datetime.now(timezone.utc)")
    return aware_moment.astimezone(timezone.utc).date()


def current_utc_date() -> date:
    """Return today's UTC date. The checker reads the clock only here."""
    return utc_date(datetime.now(timezone.utc))


def valid_revision(value: str) -> bool:
    """Return whether a CLI revision is safe to pass to Git."""
    return bool(value and not value.startswith("-") and REVISION_PATTERN.fullmatch(value))


def _version_key(match: re.Match[str]) -> tuple:
    """Return the numeric ordering key for a version heading."""
    prerelease = match.group(4)
    if prerelease is None:
        return (*[int(match.group(index)) for index in range(1, 4)], 1, ())
    tokens = tuple(
        (0, int(token)) if token.isdigit() else (1, token)
        for token in prerelease.split("."))
    return (*[int(match.group(index)) for index in range(1, 4)], 0, tokens)


def _parse_release_date(raw_date: str) -> date | None:
    """Return the calendar date of a heading, or None when it does not exist."""
    try:
        return date.fromisoformat(raw_date)
    except ValueError:
        return None


def _collect_headings(lines: list[str]) -> tuple[list[str], list[ReleaseHeading]]:
    """Return heading findings and the versioned headings in file order."""
    findings: list[str] = []
    release_headings: list[ReleaseHeading] = []
    for line_number, line in enumerate(lines, 1):
        if UNRELEASED_PATTERN.fullmatch(line):
            findings.append(f"line {line_number}: [Unreleased] is not allowed")
            continue
        if not line.startswith("## ["):
            continue
        match = VERSION_PATTERN.fullmatch(line)
        if not match:
            if HEADING_PATTERN.fullmatch(line):
                findings.append(f"line {line_number}: invalid version heading")
            continue
        release_date = _parse_release_date(match.group(5))
        if release_date is None:
            findings.append(f"line {line_number}: invalid release date {match.group(5)}; "
                            "use a real UTC calendar date as YYYY-MM-DD")
        release_headings.append(ReleaseHeading(line_number, _version_key(match), release_date))
    return findings, release_headings


def _version_findings(release_headings: list[ReleaseHeading]) -> list[str]:
    """Return findings for version order and uniqueness."""
    versions = [heading.version_key for heading in release_headings]
    findings: list[str] = []
    if versions != sorted(versions, reverse=True):
        findings.append("version headings must be in descending order")
    if len(set(versions)) != len(versions):
        findings.append("version headings must be unique")
    return findings


def _entry_findings(lines: list[str], release_headings: list[ReleaseHeading]) -> list[str]:
    """Return findings for releases without any entry text."""
    findings: list[str] = []
    for index, heading in enumerate(release_headings):
        end = (release_headings[index + 1].line_number - 1
               if index + 1 < len(release_headings) else len(lines))
        if not any(line.strip() for line in lines[heading.line_number:end]):
            findings.append(f"line {heading.line_number}: release needs a versioned entry")
    return findings


def _date_findings(release_headings: list[ReleaseHeading]) -> list[str]:
    """Return findings unless dates strictly decrease down the file."""
    findings: list[str] = []
    last_dated_heading = None
    for heading in release_headings:
        if heading.release_date is None:
            continue
        if last_dated_heading and heading.release_date >= last_dated_heading.release_date:
            relation = ("repeats line {}; merge same-day entries under the newest version"
                        if heading.release_date == last_dated_heading.release_date
                        else "is later than line {}; dates must not go back in time")
            findings.append(f"line {heading.line_number}: release date {heading.release_date} "
                            + relation.format(last_dated_heading.line_number))
        last_dated_heading = heading
    return findings


def _scan(changelog_text: str) -> tuple[list[str], ReleaseHeading | None]:
    """Return all findings and the top versioned heading of one changelog."""
    lines = changelog_text.splitlines()
    findings, release_headings = _collect_headings(lines)
    if not release_headings:
        findings.append("changelog has no versioned release heading")
        return findings, None
    findings.extend(_version_findings(release_headings))
    findings.extend(_entry_findings(lines, release_headings))
    findings.extend(_date_findings(release_headings))
    return findings, release_headings[0]


def find_violations(text: str) -> list[str]:
    """Return blocking findings for one changelog document."""
    findings, _top_heading = _scan(text)
    return findings


def check_file(path: Path) -> int:
    """Print findings for a changelog path and return a process code."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"error: cannot read {path}: {error}", file=sys.stderr)
        return 1
    findings = find_violations(text)
    for finding in findings:
        print(f"error: {path}: {finding}", file=sys.stderr)
    if findings:
        return 1
    print(f"valid changelog: {path}")
    return 0


def find_range_violations(base: str, head: str, changed: list[str],
                          today: date | None = None) -> list[str]:
    """Return findings for a pull-request changelog range."""
    findings, head_top_heading = _scan(head)
    _base_findings, base_top_heading = _scan(base)
    if "CHANGELOG.md" not in changed:
        findings.append("changed files require CHANGELOG.md")
    if head_top_heading is None:
        findings.append("head changelog requires a versioned release")
        return findings
    if base_top_heading is not None and head_top_heading.version_key <= base_top_heading.version_key:
        findings.append("head changelog version must exceed the base version")
    head_release_date = head_top_heading.release_date
    base_release_date = base_top_heading.release_date if base_top_heading else None
    if head_release_date and base_release_date and head_release_date < base_release_date:
        findings.append(f"head release date {head_release_date} goes back in time "
                        f"before the base date {base_release_date}")
    if head_release_date and today and head_release_date > today:
        findings.append(f"head release date {head_release_date} is after the UTC date {today}")
    return findings


def _single_line(error: Exception) -> str:
    """Return exception text with line breaks removed for one-line output."""
    return " ".join(str(error).split())


def check_range(repository: Path, base: str, head: str, today: date | None = None) -> int:
    """Require a valid, newer changelog across a Git revision range."""
    if not valid_revision(base) or not valid_revision(head):
        print("error: invalid Git revision", file=sys.stderr)
        return 1
    try:
        changed = run_git(
            repository, ["diff", "--name-only", f"{base}..{head}"], check=True,
        ).stdout.splitlines()
        base_result = run_git(
            repository, ["show", f"{base}:CHANGELOG.md"], check=False,
        )
        base_text = base_result.stdout if base_result.returncode == 0 else ""
        head_text = run_git(
            repository, ["show", f"{head}:CHANGELOG.md"], check=True,
        ).stdout
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: cannot inspect changelog revision range: {_single_line(error)}; "
              "fetch both revisions and retry", file=sys.stderr)
        return 1
    findings = find_range_violations(base_text, head_text, changed, today or current_utc_date())
    for finding in findings:
        print(f"error: CHANGELOG.md: {finding}", file=sys.stderr)
    return 1 if findings else 0


def _read_staged(repository: Path) -> tuple[list[str], str, str]:
    """Return staged paths, the staged changelog, and the HEAD changelog."""
    staged_paths = run_git(
        repository, ["diff", "--cached", "--name-only", "--diff-filter=ACMRT"],
        check=True,
    ).stdout.splitlines()
    # Git cannot show an unstaged index entry; _staged_findings reports it.
    if "CHANGELOG.md" not in staged_paths:
        return staged_paths, "", ""
    staged_changelog_text = run_git(repository, ["show", ":CHANGELOG.md"], check=True).stdout
    head_result = run_git(repository, ["show", "HEAD:CHANGELOG.md"], check=False)
    return staged_paths, staged_changelog_text, head_result.stdout


def _staged_findings(staged_paths: list[str], staged_changelog_text: str,
                     head_changelog_text: str, today: date) -> list[str]:
    """Return findings for a staged changelog against HEAD and the UTC date."""
    if "CHANGELOG.md" not in staged_paths:
        return ["staged changes require a versioned CHANGELOG.md entry"]
    findings, staged_top_heading = _scan(staged_changelog_text)
    if findings:
        return findings
    _head_findings, head_top_heading = _scan(head_changelog_text)
    if head_top_heading and staged_top_heading.version_key <= head_top_heading.version_key:
        return ["CHANGELOG.md version must advance with staged changes"]
    if staged_top_heading.release_date != today:
        return [f"top release date {staged_top_heading.release_date} must be today's UTC "
                f"date {today}; ask the active human if the date is in doubt"]
    return []


def check_staged(repository: Path, today: date | None = None) -> int:
    """Require a changed versioned changelog entry for staged changes."""
    try:
        staged_paths, staged_changelog_text, head_changelog_text = _read_staged(repository)
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: cannot inspect staged changelog: {_single_line(error)}; "
              "stage CHANGELOG.md and retry", file=sys.stderr)
        return 1
    if not staged_paths:
        return 0
    findings = _staged_findings(staged_paths, staged_changelog_text, head_changelog_text,
                                today or current_utc_date())
    for finding in findings:
        print(f"error: CHANGELOG.md: {finding}", file=sys.stderr)
    return 1 if findings else 0


def main() -> int:
    """Run the changelog checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="CHANGELOG.md")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base")
    parser.add_argument("--head")
    args = parser.parse_args()
    if args.base or args.head:
        if not args.base or not args.head:
            parser.error("--base and --head must be used together")
        return check_range(Path(args.repo).resolve(), args.base, args.head)
    if args.staged:
        return check_staged(Path(args.repo).resolve())
    return check_file(Path(args.path))


if __name__ == "__main__":
    sys.exit(main())
