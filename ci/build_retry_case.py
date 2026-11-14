#!/usr/bin/env python3
"""Build the retry review envelope after a response fails validation.

Usage: build_retry_case.py CASE_FILE ERROR_FILE OUTPUT_FILE

The retry envelope copies the first envelope and adds the validator problems
to TRUSTED_HOOK_CONTEXT. REVIEW_TARGET stays byte-identical. Only validator
lines from a fixed allowlist enter the trusted channel. Any other line becomes
a fixed placeholder. Model-controlled text in the error file therefore cannot
reach trusted context, whatever the validator prints.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# The case builder lives beside this script. Tests import this module from
# the repository root. The explicit path entry serves both entry points.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_pr_case  # resolved through the path set above

# Reading a bounded prefix keeps a runaway error file out of memory.
MAX_ERROR_FILE_CHARS = 8_000
EXPECTED_ARGUMENTS = 3
VALIDATOR_PREFIX = "invalid PR review response: "
# Every fixed ResponseError message in scripts/check_pr_review_response.py.
# tests/test_build_retry_case.py fails when the validator gains a message
# missing here.
KNOWN_PROBLEMS = frozenset({
    "response is empty",
    "final VERDICT line is malformed",
    "no VERDICT line at column zero",
    "VERDICT_JSON is not valid JSON",
    "VERDICT_JSON is not an object",
    "no VERDICT_JSON line",
    "VERDICT_JSON must be the final report line",
    "VERDICT_JSON must immediately follow the final VERDICT line",
    "finding has an invalid shape",
    "finding has an invalid severity",
    "finding has an invalid class",
    "finding has an invalid file",
    "finding has an invalid line",
    "finding has an invalid title",
    "response exceeds the configured limit",
    "VERDICT_JSON has an invalid shape",
    "VERDICT_JSON does not match the PR verdict",
    "findings is not a list",
})
# The validator lists at most five narration line numbers.
NARRATION_PROBLEM = re.compile(
    r"process narration appears on report line\(s\): [0-9]{1,7}(?:, [0-9]{1,7}){0,4}"
)
RETRY_NOTE = (
    "Retry attempt. The previous report failed structural validation. Emit a corrected "
    "full report that satisfies the reporting contract. The validator problems follow."
)
UNKNOWN_PROBLEM = "validator problem unavailable"


def is_known_problem(message: str) -> bool:
    """Return whether a validator message comes from the fixed allowlist."""
    return message in KNOWN_PROBLEMS or NARRATION_PROBLEM.fullmatch(message) is not None


def select_problems(text: str) -> list[str]:
    """Return the allowlisted validator lines, deduplicated, in input order.

    A line outside the allowlist adds UNKNOWN_PROBLEM once. An input without
    an allowlisted line yields UNKNOWN_PROBLEM alone.
    """
    selected: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        message = line.removeprefix(VALIDATOR_PREFIX)
        known = line.startswith(VALIDATOR_PREFIX) and is_known_problem(message)
        entry = VALIDATOR_PREFIX + message if known else UNKNOWN_PROBLEM
        if entry not in selected:
            selected.append(entry)
    return selected or [UNKNOWN_PROBLEM]


def build_retry_case(case: object, problems: str) -> dict[str, object]:
    """Return a copy of the case with the allowlisted problems in trusted context."""
    if not isinstance(case, dict):
        raise ValueError("case is not an object")
    trusted = case.get("TRUSTED_HOOK_CONTEXT")
    if not isinstance(trusted, dict) or not isinstance(case.get("REVIEW_TARGET"), dict):
        raise ValueError("case lacks TRUSTED_HOOK_CONTEXT or REVIEW_TARGET")
    text = "\n".join([RETRY_NOTE, *select_problems(problems)])
    retry_trusted = {**trusted, "text": text, "sha256": build_pr_case._digest(text)}
    return {**case, "TRUSTED_HOOK_CONTEXT": retry_trusted}


def main(arguments: list[str]) -> int:
    """Write the retry envelope from the first case and the validator output."""
    if len(arguments) != EXPECTED_ARGUMENTS:
        print("usage: build_retry_case.py CASE_FILE ERROR_FILE OUTPUT_FILE", file=sys.stderr)
        return 2
    case_path, error_path, output_path = (Path(argument) for argument in arguments)
    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        with error_path.open(encoding="utf-8", errors="replace") as error_file:
            problems = error_file.read(MAX_ERROR_FILE_CHARS)
        retry = build_retry_case(case, problems)
        output_path.write_text(json.dumps(retry, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        print(f"retry case construction failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
