#!/usr/bin/env python3
"""Enforce persist-credentials: false on checkout steps (Rule 11)."""
import sys
from pathlib import Path

import yaml

try:
    from scripts._yaml_lines import (
        exception_comment_lines, load_documents, mapping_line_numbers,
        walk_mappings,
    )
except ModuleNotFoundError:
    from _yaml_lines import (
        exception_comment_lines, load_documents, mapping_line_numbers,
        walk_mappings,
    )

EXCEPTION_COMMENT = (
    "# persist-credentials: true: this job ",
    " (Rule 11 exception).",
)
_MISSING = object()


def _is_exception_comment(line: str) -> bool:
    """Return whether a line has the exact Rule 11 exception form."""
    stripped = line.strip()
    prefix, suffix = EXCEPTION_COMMENT
    if not stripped.startswith(prefix) or not stripped.endswith(suffix):
        return False
    reason = stripped[len(prefix):-len(suffix)]
    return bool(reason.strip())


def _checkout_violation(
    step: dict, lines: list[str], exception_lines: set[int], path: str,
) -> str | None:
    """Return a violation for one checkout step, if needed."""
    action = step.get("uses")
    if not isinstance(action, str) or not action.lower().startswith(
        "actions/checkout@"
    ):
        return None
    settings = step.get("with")
    value = _MISSING
    if isinstance(settings, dict):
        value = settings.get("persist-credentials", _MISSING)
    if value is False:
        return None
    has_exception = bool(
        exception_lines & mapping_line_numbers(lines, step))
    if (value is _MISSING or value is True) and has_exception:
        return None
    key_lines = getattr(step, "key_lines", {})
    number = key_lines.get("uses", getattr(step, "line", 0)) + 1
    return (
        f"{path}:{number}: actions/checkout missing "
        "persist-credentials: false (Rule 11)"
    )


def find_violations(text: str, path: str) -> list[str]:
    """Return violations without propagating malformed YAML errors."""
    try:
        documents = load_documents(text)
        exception_lines = exception_comment_lines(text, _is_exception_comment)
    except yaml.YAMLError:
        return [f"{path}: malformed YAML cannot be checked (Rule 11)"]
    lines = text.splitlines()
    return [
        violation
        for step in walk_mappings(documents)
        if (
            violation := _checkout_violation(
                step, lines, exception_lines, path)
        ) is not None
    ]


def main() -> int:
    """Check each workflow file. Return 0 when all are clean."""
    paths = sys.argv[1:]
    if not paths:
        print("usage: check_persist_credentials.py FILE [FILE ...]", file=sys.stderr)
        return 1
    violations = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        try:
            violations.extend(find_violations(text, path))
        except Exception as error:
            # Top-level boundary: fail closed and name a checker defect as one.
            print(f"error: {path}: internal checker failure "
                  f"({type(error).__name__}). Recovery: report the file and "
                  "command to the repository maintainers.", file=sys.stderr)
            return 1
    if not violations:
        return 0
    for message in violations:
        print(message, file=sys.stderr)
    print(
        "fix: add `with: persist-credentials: false`, or the Rule 11 "
        "exception comment",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
