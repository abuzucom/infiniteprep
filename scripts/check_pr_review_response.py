#!/usr/bin/env python3
"""Validate the machine-readable PR review response contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

VERDICT_PATTERN = re.compile(
    r"^VERDICT:\s*(APPROVE|BLOCK|NEEDS-HUMAN)(?:\s+-\s+.*)?$"
)
VERDICT_PREFIX = "VERDICT:"
CLASS_PATTERN = re.compile(r"^2\.\d+$")
VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
FINDING_KEYS = {"severity", "class", "file", "line", "title"}
RESPONSE_LIMIT = 1_000_000
# A bare "i" or "we" opener is narration unless a period follows, as in
# "i.e.". Section labels such as "Trace:" or "Inspect results:" are
# impersonal. Narration with those verbs starts with "I", "we", or "let me".
PROCESS_NARRATION_PATTERNS = (
    re.compile(
        r"^\s*(?:(?:[-*+])\s+|\d+[.)]\s+)?(?:i|we)\b(?!\.)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:(?:[-*+])\s+|\d+[.)]\s+)?(?:let me\b|let's\b|"
        r"should i\b|could i\b|would i\b|"
        r"do i\b|did i\b|am i\b|have i\b|wait(?:,|\s|$)|"
        r"actually(?:,|\s|$)|on second thought\b|reconsider(?:ing)?\b|"
        r"check whether\b|check if\b|"
        r"now i\b|first i\b|what if\b|maybe (?:i|we)\b|"
        r"perhaps (?:i|we)\b|should (?:this|that|it|the)\b|"
        r"could (?:this|that|it)\b|would (?:this|that|it)\b|"
        r"is this\b|does this\b)",
        re.IGNORECASE,
    ),
)
PRIOR_FINDING_STATUS_PATTERN = re.compile(
    r"\b(?:prior|previous|earlier)\b[^\n]{0,120}"
    r"\b(?:resolved|still open|remains open)\b",
    re.IGNORECASE,
)


class ResponseError(ValueError):
    """Report a response-contract failure."""


def _last_verdict(response: str) -> tuple[str, str]:
    """Return the verdict from the line the workflow gate reads.

    The gate runs `grep -E '^VERDICT:' | tail -1`, so it reads the last
    newline-delimited line that starts with VERDICT: at column zero.
    Validating any other line lets a response pass with one verdict while
    the gate acts on another. Splitting on newline alone matches grep:
    splitlines() also breaks on carriage returns and form feeds, which grep
    keeps inside one line.
    """
    lines = response.split("\n")
    if not any(line.strip() for line in lines):
        raise ResponseError("response is empty")
    for line in reversed(lines):
        if not line.startswith(VERDICT_PREFIX):
            continue
        candidate = line.rstrip()
        match = VERDICT_PATTERN.fullmatch(candidate)
        if not match:
            raise ResponseError("final VERDICT line is malformed")
        return match.group(1), candidate
    raise ResponseError("no VERDICT line at column zero")


def _parse_json_line(lines: list[str]) -> dict[str, Any]:
    json_index = None
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.startswith("VERDICT_JSON:"):
            continue
        json_index = index
        payload = line[len("VERDICT_JSON:"):].lstrip()
        try:
            result = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ResponseError("VERDICT_JSON is not valid JSON") from error
        if not isinstance(result, dict):
            raise ResponseError("VERDICT_JSON is not an object")
        break
    if json_index is None:
        raise ResponseError("no VERDICT_JSON line")
    nonempty_indexes = [
        index for index, line in enumerate(lines) if line.strip()
    ]
    if nonempty_indexes[-1] != json_index:
        raise ResponseError("VERDICT_JSON must be the final report line")
    verdict_indexes = [
        index for index, line in enumerate(lines)
        if line.startswith(VERDICT_PREFIX)
    ]
    if not verdict_indexes or json_index != verdict_indexes[-1] + 1:
        raise ResponseError("VERDICT_JSON must immediately follow the final VERDICT line")
    return result


def process_narration_lines(response: str) -> list[int]:
    """Return line numbers with clear first-person process narration."""
    lines = response.split("\n")
    verdict_indexes = [
        index for index, line in enumerate(lines)
        if line.startswith(VERDICT_PREFIX)
    ]
    if not verdict_indexes:
        return []
    narration_lines = []
    fenced = False
    for index, line in enumerate(lines[:verdict_indexes[-1]]):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced or stripped.startswith(">"):
            continue
        if any(pattern.match(line) for pattern in PROCESS_NARRATION_PATTERNS):
            narration_lines.append(index + 1)
    return narration_lines


def _validate_finding(finding: Any) -> None:
    if not isinstance(finding, dict) or set(finding) != FINDING_KEYS:
        raise ResponseError("finding has an invalid shape")
    if finding["severity"] not in VALID_SEVERITIES:
        raise ResponseError("finding has an invalid severity")
    if not isinstance(finding["class"], str) or not CLASS_PATTERN.fullmatch(
        finding["class"]
    ):
        raise ResponseError("finding has an invalid class")
    if not isinstance(finding["file"], str) or not finding["file"]:
        raise ResponseError("finding has an invalid file")
    if (
        not isinstance(finding["line"], int)
        or isinstance(finding["line"], bool)
        or finding["line"] < 1
    ):
        raise ResponseError("finding has an invalid line")
    if not isinstance(finding["title"], str) or not finding["title"]:
        raise ResponseError("finding has an invalid title")


def validate_response(response: str) -> tuple[str, dict[str, Any]]:
    """Validate one bounded PR response and return its verdict and JSON."""
    if len(response.encode("utf-8")) > RESPONSE_LIMIT:
        raise ResponseError("response exceeds the configured limit")
    verdict, _line = _last_verdict(response)
    narration_lines = process_narration_lines(response)
    if narration_lines:
        line_numbers = ", ".join(str(number) for number in narration_lines[:5])
        raise ResponseError(
            f"process narration appears on report line(s): {line_numbers}"
        )
    payload = _parse_json_line(response.split("\n"))
    if set(payload) != {"mode", "verdict", "findings"}:
        raise ResponseError("VERDICT_JSON has an invalid shape")
    if payload["mode"] != "PR" or payload["verdict"] != verdict:
        raise ResponseError("VERDICT_JSON does not match the PR verdict")
    findings = payload["findings"]
    if not isinstance(findings, list):
        raise ResponseError("findings is not a list")
    for finding in findings:
        _validate_finding(finding)
    return verdict, payload


def main() -> int:
    """Validate the response file and print a normalized verdict."""
    if len(sys.argv) != 2:
        print("usage: check_pr_review_response.py RESPONSE_FILE", file=sys.stderr)
        return 2
    try:
        # read_text() translates a bare carriage return into a newline. The
        # gate's grep does not, so decode the raw bytes instead.
        response = Path(sys.argv[1]).read_bytes().decode("utf-8")
        verdict, _payload = validate_response(response)
    except (OSError, UnicodeError, ResponseError) as error:
        print(f"invalid PR review response: {error}", file=sys.stderr)
        return 1
    print(f"VERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
