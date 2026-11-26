#!/usr/bin/env python3
"""Deny an agent turn that delegates a recovery command to the active human."""
import argparse
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import _gate_core as core
except ImportError as error:
    print(f"shared gate core import failed ({error})", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

try:
    import check_anti_delegation as checker
except ImportError:
    checker = None

GATE = "enforce_anti_delegation.py"
MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
MAX_MESSAGE_BYTES = 256 * 1024
MAX_FINDINGS = 5
OUTPUT_FIELDS = (
    "assistant_message",
    "last_assistant_message",
    "message",
    "output_text",
    "content",
    "text",
)


def _read_regular(path: str, limit: int) -> str:
    """Return bounded UTF-8 content from one regular non-symlink file."""
    details = os.lstat(path)
    if not stat.S_ISREG(details.st_mode) or details.st_size > limit:
        raise OSError("metadata is not a bounded regular file")
    with open(path, encoding="utf-8") as handle:
        return handle.read(limit + 1)


def _block_text(value: object) -> str:
    """Return the text of one content value that holds strings or text blocks."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts = []
    for block in value:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _assistant_text(record: dict) -> str:
    """Return the assistant text of one transcript record."""
    message = record.get("message")
    if isinstance(message, dict):
        role = message.get("role")
        if role == "assistant":
            return _block_text(message.get("content"))
    role = record.get("role") or record.get("source")
    if role == "assistant":
        return _block_text(record.get("content"))
    return ""


def _transcript_text(path: str) -> str:
    """Return the last assistant message of one transcript file."""
    content = _read_regular(path, MAX_TRANSCRIPT_BYTES)
    for line in reversed(content.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        text = _assistant_text(record)
        if text:
            return text
    return ""


def _payload_texts(payload: dict) -> list[str]:
    """Return every agent output text that one payload carries."""
    texts = []
    for field in OUTPUT_FIELDS:
        value = payload.get(field)
        text = _block_text(value)
        if text:
            texts.append(text[:MAX_MESSAGE_BYTES])
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        try:
            text = _transcript_text(transcript_path)
        except (OSError, UnicodeError):
            text = ""
        if text:
            texts.append(text[:MAX_MESSAGE_BYTES])
    return texts


def _findings(payload: dict) -> list:
    """Return every delegation finding in the agent output of one payload."""
    if checker is None:
        return []
    findings = []
    for text in _payload_texts(payload):
        findings.extend(checker.scan_text_for_delegation(text))
    return findings


def _reason(findings: list) -> str:
    """Return the bounded reason that names every finding."""
    parts = []
    for finding in findings[:MAX_FINDINGS]:
        parts.append(
            f"line {finding.line_number}: {core.sanitize(finding.match_text)}"
        )
    return (
        "The agent output delegates a recovery command to the active human. "
        "Run the command yourself. Remove the delegation and finish again. "
        + " | ".join(parts)
    )


def handle_stop_event(payload: dict) -> int:
    """Block one completion attempt while the output delegates a command."""
    if payload.get("stop_hook_active") is True:
        return 0
    findings = _findings(payload)
    if not findings:
        return 0
    print(json.dumps({"decision": "block", "reason": _reason(findings)}))
    return 0


def handle_pre_tool_event(payload: dict) -> int:
    """Deny one tool call while the payload carries a delegation."""
    findings = _findings(payload)
    if not findings:
        return 0
    return core.emit(GATE, "deny", _reason(findings))


def main(argv: list[str] | None = None) -> int:
    """Scan one payload and deny the turn when it delegates a command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client",
        choices=("claude", "codex", "gemini", "antigravity"),
        default="claude",
    )
    args = parser.parse_args(argv)
    payload = core.read_payload(empty_is_session_start=True)
    if payload is None:
        print(
            f"blocked by hooks/{GATE}: the payload could not be read.",
            file=sys.stderr,
        )
        return 2
    event = payload.get("hook_event_name", "")
    if event in ("Stop", "SubagentStop"):
        return handle_stop_event(payload)
    if event in ("PreToolUse", "BeforeTool"):
        return handle_pre_tool_event(payload)
    findings = _findings(payload)
    if findings:
        print(json.dumps({"decision": "block", "reason": _reason(findings)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
