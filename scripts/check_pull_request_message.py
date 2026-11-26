#!/usr/bin/env python3
"""Warn about pull request title and description prose."""
import json
import os
import sys
from pathlib import Path

try:
    from scripts import check_commit_message
    from scripts._pr_event import (
        DEPENDABOT_LOGIN, MAX_BODY_LENGTH, MAX_EVENT_BYTES, MAX_TITLE_LENGTH, load_pull_request)
    from scripts.prose_policy import find_violations
except ImportError:
    import check_commit_message
    from _pr_event import (
        DEPENDABOT_LOGIN, MAX_BODY_LENGTH, MAX_EVENT_BYTES, MAX_TITLE_LENGTH, load_pull_request)
    from prose_policy import find_violations

# check_agent_prose_gate imports the loader under this name.
_load_event = load_pull_request


def check_event(path: str | Path) -> int:
    """Print sanitized advisory findings from one GitHub event file."""
    try:
        title, body, author = _load_event(Path(path))
        findings = []
        if author != DEPENDABOT_LOGIN:
            findings.extend(
                check_commit_message.find_title_violations(title)
            )
        findings.extend(
            find_violations(
                check_commit_message.mask_type_prefix(title),
                "pull_request.title",
            )
        )
        findings.extend(find_violations(body, "pull_request.body"))
        for message in findings:
            print(message)
        if not findings:
            print("no pull-request prose findings found")
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"error: pull request prose check failed: {error}", file=sys.stderr)
        return 1


def main() -> int:
    """Check the GitHub event path supplied by the runner environment."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("error: GITHUB_EVENT_PATH is unset", file=sys.stderr)
        return 1
    return check_event(event_path)


if __name__ == "__main__":
    sys.exit(main())
