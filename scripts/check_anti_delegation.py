#!/usr/bin/env python3
"""Detect delegating recovery commands or manual actions to humans."""
import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass

MAX_SCAN_BYTES = 256 * 1024
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

POLICY_EXEMPT_PREFIXES = (
    "warning:",
    "warning -",
    "policy prohibits",
    "policy forbids",
    "never ask",
    "do not ask",
    "never delegate",
    "do not delegate",
    "strictly prohibited",
    "strictly forbids",
)

COMMENT_PREFIXES = ("#", "//", "/*", "*", '"""', "'''")

# A delegation needs both parts. The directive names the human who receives the
# request. The payload names the command that the request carries. A sentence
# with only one part stays ordinary prose.
HUMAN_DIRECTIVE_PATTERNS = (
    r"please\b",
    r"\byou\s+(?:can|should|need\s+to|must|may)\b",
    r"\b(?:the\s+)?(?:active\s+)?(?:user|human|operator)\s+"
    r"(?:can|should|need\s+to|must|may)\b",
    r"\bask(?:ing)?\s+(?:the\s+)?(?:active\s+)?(?:user|human|operator)\b",
    r"\b(?:instruct|tell|have)\s+(?:the\s+)?(?:active\s+)?(?:user|human|operator)\b",
    r"\bfor\s+(?:the\s+)?(?:active\s+)?(?:user|human|operator)\b",
    r"\b(?:run|execute|type)\s+(?:the\s+following|this|these)\b",
    r"\bdelegate\b",
    r"^(?:run|execute|type|perform|do)\b",
)

# Ordered by priority. The first matching category names the finding.
PAYLOAD_PATTERNS = (
    (
        "manual_recovery_delegation",
        (
            r"\bmanual\s+recovery\b",
            r"\brecover(?:y)?\s+manually\b",
            r"\bmanually\s+recover\b",
            r"\brecovery\s+(?:action|command|work|task)\b",
        ),
    ),
    (
        "git_recovery_delegation",
        (r"\bgit\s+(?:merge|rebase|switch|checkout|branch|reset)\b",),
    ),
    (
        "command_delegation",
        (
            r"\b(?:in|into|using|with|inside)\s+(?:the\s+)?"
            r"(?:powershell|terminal|bash|shell|cmd|command\s+prompt)\b",
        ),
    ),
)

COMPILED_DIRECTIVES = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in HUMAN_DIRECTIVE_PATTERNS
)
COMPILED_PAYLOADS = tuple(
    (category, tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns))
    for category, patterns in PAYLOAD_PATTERNS
)


@dataclass
class DelegationFinding:
    """Record of a detected delegation violation."""

    category: str
    pattern: str
    match_text: str
    line_number: int


def _sentences(line: str) -> list[str]:
    """Split one line into the sentences that carry separate statements."""
    return [part.strip() for part in SENTENCE_SPLIT.split(line) if part.strip()]


def _is_exempt_sentence(sentence: str) -> bool:
    """Return whether one sentence states the prohibition instead of breaching it."""
    lowered = sentence.lower()
    for prefix in POLICY_EXEMPT_PREFIXES:
        if lowered.startswith(prefix):
            return True
    return False


def _directive_match(sentence: str) -> str | None:
    """Return the human directive that one sentence carries."""
    for pattern in COMPILED_DIRECTIVES:
        match = pattern.search(sentence)
        if match:
            return match.group(0)
    return None


def _payload_match(sentence: str) -> tuple[str, str] | None:
    """Return the category and matched text of the first matching payload."""
    for category, patterns in COMPILED_PAYLOADS:
        for pattern in patterns:
            match = pattern.search(sentence)
            if match:
                return category, match.group(0)
    return None


def scan_text_for_delegation(text: str) -> list[DelegationFinding]:
    """Scan input text against every anti-delegation category."""
    findings: list[DelegationFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        for sentence in _sentences(stripped):
            if _is_exempt_sentence(sentence):
                continue
            if _directive_match(sentence) is None:
                continue
            payload = _payload_match(sentence)
            if payload is None:
                continue
            findings.append(
                DelegationFinding(
                    category=payload[0],
                    pattern=payload[1],
                    match_text=payload[1],
                    line_number=line_number,
                )
            )
    return findings


def read_content(path: str) -> str:
    """Return bounded UTF-8 content from one file or standard input."""
    if path == "-":
        return sys.stdin.read(MAX_SCAN_BYTES)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read(MAX_SCAN_BYTES)


def main(argv: list[str] | None = None) -> int:
    """Scan every requested file and report every delegation finding."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="*",
        default=["-"],
        help="files to scan (default: standard input)",
    )
    parser.add_argument("--json", action="store_true", help="output JSON findings")
    args = parser.parse_args(argv)
    records = []
    for path in args.files:
        try:
            content = read_content(path)
        except OSError as error:
            print(f"anti-delegation scan failed: {error}", file=sys.stderr)
            return 2
        for finding in scan_text_for_delegation(content):
            record = asdict(finding)
            record["path"] = path
            records.append(record)
    if args.json:
        print(json.dumps(records, indent=2))
    else:
        for record in records:
            print(
                f"{record['path']}:{record['line_number']}: "
                f"[{record['category']}] {record['match_text']}"
            )
    return 1 if records else 0


if __name__ == "__main__":
    sys.exit(main())
