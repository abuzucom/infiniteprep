#!/usr/bin/env python3
"""Block agent-attributed prose findings that stay advisory for humans.

scripts/check_us_spelling.py, scripts/check_english_only.py,
scripts/check_hedging.py, and scripts/check_pull_request_message.py always
exit 0: their findings stay advisory for human-authored prose, since the
pattern checks misfire on legitimate human writing and semantic review
covers what they miss. AGENTS.md's rules bind every AI system regardless,
so this check reuses the same analyzers and fails on their findings, but
only for commits and pull requests that disclose agent authorship.

Attribution reuses scripts/check_commit_attribution.py::has_agent_label,
the same name-only Assisted-by/Co-authored-by trailer signal already used
to apply stricter identity rules to agent-labeled commits, and
scripts/check_banned_agents.py::_extract_pr_disclosures for a disclosure
in the pull request description alone. A pull request with no disclosure
anywhere costs nothing extra here: no file is read and no analyzer runs.

Humans publish releases manually. The gate therefore also blocks release
words outside Markdown code in agent-labeled commit messages and in the
title and body of an agent-disclosed pull request.

Known limitation: an agent that never discloses itself is invisible to
this signal, the same documented gap in check_banned_agents.py ("a banned
agent committing under a human's own git identity... is invisible to this
check. No mechanical check can close that gap"). Removing or editing a
disclosure trailer specifically to stop this check from firing is not a
valid fix; see docs/agent-policy/enforcement.md. Fix the flagged prose.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts.check_banned_agents import _extract_pr_disclosures, load_commits
    from scripts.check_commit_attribution import has_agent_label
    from scripts.check_commit_message import find_title_violations, mask_type_prefix
    from scripts.check_english_only import find_violations as find_english_violations
    from scripts.check_pull_request_message import _load_event
    from scripts.check_us_spelling import find_violations as find_spelling_violations
    from scripts.prose_policy import (
        find_violations as find_prose_violations, load_denylist, mask_markdown_code,
    )
except ModuleNotFoundError:
    from check_banned_agents import _extract_pr_disclosures, load_commits
    from check_commit_attribution import has_agent_label
    from check_commit_message import find_title_violations, mask_type_prefix
    from check_english_only import find_violations as find_english_violations
    from check_pull_request_message import _load_event
    from check_us_spelling import find_violations as find_spelling_violations
    from prose_policy import (
        find_violations as find_prose_violations, load_denylist, mask_markdown_code,
    )

# Must track the file list .github/workflows/sync-check.yml passes to
# check_us_spelling.py, check_english_only.py, and check_hedging.py.
TARGET_FILES = (
    "AGENTS.md",
    "README.md",
    "CHANGELOG.md",
    "docs/project-orientation.md",
    "docs/template-drift.md",
    "plan/HANDOFF.md.example",
    "SECURITY.md",
    "CONTRIBUTING.md",
)

DEPENDABOT_LOGIN = "dependabot[bot]"

ANTI_BYPASS_NOTICE = (
    "fix the prose above; do not remove or edit the Assisted-by/"
    "Co-authored-by trailer to evade this check"
)

# A pull request body has no trailer paragraph. The prose analyzers read a
# required disclosure line as a sentence. "Assisted-by: Claude Opus 5.5"
# matches the colon clause pattern because "Opus" ends in "s". Only the
# disclosure key and its colon are blanked. The value stays in place, so the
# analyzers still check every word after the key.
BOLD_MARKER = "*"

# Humans publish releases manually. AGENTS.md forbids agent prose that calls a
# version bump a release without active-human consent. Code spans stay exempt.
# File names and commands such as `scripts/release.py` and `gh release` need
# the word.
RELEASE_WORD_PATTERN = re.compile(r"(?<![A-Za-z])releas(?:e|es|ed|ing)(?![A-Za-z])", re.IGNORECASE)
RELEASE_FINDING_DETAIL = (
    "release wording (call a version bump a version bump; "
    "humans publish releases)"
)


def _mask_disclosure_key(line: str) -> str:
    """Replace a disclosure key prefix with spaces and keep the value."""
    if not _extract_pr_disclosures(line):
        return line
    value = line[line.index(":") + 1:].lstrip(BOLD_MARKER)
    return " " * (len(line) - len(value)) + value


def mask_disclosure_lines(body: str) -> str:
    """Blank each disclosure key and keep every value, line, and column.

    Spaces keep the line and column numbers in every finding accurate.
    """
    return "\n".join(_mask_disclosure_key(line) for line in body.split("\n"))


def find_release_wording(text: str, location: str) -> list[str]:
    """Return one finding per release word outside Markdown code."""
    prose = mask_markdown_code(text)
    findings = []
    for match in RELEASE_WORD_PATTERN.finditer(prose):
        line = prose.count("\n", 0, match.start()) + 1
        column = match.start() - prose.rfind("\n", 0, match.start())
        findings.append(f"warning: {location}:{line}:{column}: {RELEASE_FINDING_DETAIL}")
    return findings


def find_commit_release_findings(commits: list[dict]) -> list[str]:
    """Return release wording findings for every agent-labeled commit message."""
    findings = []
    for commit in commits:
        message = commit.get("body", "")
        if has_agent_label(message):
            findings.extend(find_release_wording(message, f"commit {commit.get('sha', '')}"))
    return findings


def is_agent_attributed(commits: list[dict], pr_body: str) -> bool:
    """Return whether any commit or the pull request description discloses an agent."""
    return (
        any(has_agent_label(commit.get("body", "")) for commit in commits)
        or bool(_extract_pr_disclosures(pr_body))
    )


def find_doc_findings(repo: str, entries: list) -> list[str]:
    """Return advisory-analyzer findings for every tracked policy document."""
    root = Path(repo)
    findings = []
    for relative_path in TARGET_FILES:
        text = (root / relative_path).read_text(encoding="utf-8")
        findings.extend(find_spelling_violations(text, relative_path))
        findings.extend(find_english_violations(text, relative_path))
        findings.extend(find_prose_violations(text, relative_path, entries=entries))
    return findings


def find_pr_findings(title: str, body: str, author: str, entries: list) -> list[str]:
    """Return advisory-analyzer findings for the pull request title and body."""
    findings = []
    if author != DEPENDABOT_LOGIN:
        findings.extend(find_title_violations(title))
    findings.extend(
        find_prose_violations(mask_type_prefix(title), "pull_request.title", entries=entries)
    )
    findings.extend(find_prose_violations(
        mask_disclosure_lines(body), "pull_request.body", entries=entries,
    ))
    findings.extend(find_release_wording(mask_type_prefix(title), "pull_request.title"))
    findings.extend(find_release_wording(mask_disclosure_lines(body), "pull_request.body"))
    return findings


def check(base: str, head: str, repo: str, event_path: str) -> int:
    """Block on advisory-analyzer findings, but only for agent-attributed work."""
    commits = load_commits(base, head, repo)
    title, body, author = _load_event(Path(event_path))

    if not is_agent_attributed(commits, body):
        print("no agent attribution found; advisory checks remain non-blocking")
        return 0

    entries = load_denylist()
    findings = find_doc_findings(repo, entries)
    findings.extend(find_pr_findings(title, body, author, entries))
    findings.extend(find_commit_release_findings(commits))

    for message in findings:
        print(message)
    if findings:
        print(ANTI_BYPASS_NOTICE)
        return 1
    print("no agent-attributed prose findings found")
    return 0


def main() -> int:
    """Check the base..head commit range for agent-attributed prose findings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base ref (exclusive)")
    parser.add_argument("--head", required=True, help="head ref (inclusive)")
    parser.add_argument("--repo", default=os.getcwd(), help="repository to inspect (default: cwd)")
    args = parser.parse_args()
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("error: GITHUB_EVENT_PATH is unset", file=sys.stderr)
        return 1
    try:
        return check(args.base, args.head, args.repo, event_path)
    except (
        OSError, subprocess.SubprocessError, UnicodeError, ValueError, json.JSONDecodeError,
    ) as error:
        print(f"error: agent prose gate failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
