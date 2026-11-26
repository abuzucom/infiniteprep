#!/usr/bin/env python3
"""Block one stop when the final message offers choices in prose only.

AGENTS.md requires each open choice to reach the active human as one
question with one recommendation. Claude Code exposes AskUserQuestion for
that. This Stop hook reads the tail of the transcript. It blocks the stop
once when the last assistant message asks the human to choose and the turn
holds no question tool call.

The hook is advisory. Phrase matching cannot prove that a message offers a
choice. Any read or parse failure passes with a note on stderr, because a
false block on every stop would trap the session.
"""
import json
import os
import stat
import sys

import _gate_core as core

GATE = "detect_prose_options.py"
TAIL_BYTES = 256 * 1024
MAX_LINE_CHARACTERS = 2000
QUESTION_TOOLS = frozenset({"AskUserQuestion", "ask_question"})
CHOICE_PHRASES = frozenset({"would you like me to", "do you want me to", "which option",
                            "which would you prefer", "let me know which", "option a",
                            "option 1", "or would you rather", "which approach"})
BLOCK_REASON = ("The final message asks the active human to choose in prose. Ask through "
                "AskUserQuestion instead. Use one question with two to four options and "
                "mark the recommended option with (Recommended).")


def _read_tail(path: str) -> str:
    """Return the decoded tail of one regular transcript file that is not a link."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode):
        os.close(descriptor)
        raise OSError("transcript is not a regular file")
    with os.fdopen(descriptor, "rb") as handle:
        start = max(0, details.st_size - TAIL_BYTES)
        handle.seek(start)
        data = handle.read(TAIL_BYTES)
    lines = data.decode("utf-8", errors="replace").splitlines(keepends=True)
    # A tail read can start inside a record. The first partial line drops.
    return "".join(lines[1:] if start else lines)


def _records(text: str) -> list:
    """Return the JSON object records of the transcript tail in order."""
    records = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _message(record: dict) -> tuple:
    """Return (role, content blocks) for one transcript record."""
    message = record.get("message")
    if not isinstance(message, dict):
        return "", []
    content = message.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    blocks = [block for block in content if isinstance(block, dict)] \
        if isinstance(content, list) else []
    return message.get("role", ""), blocks


def _is_human_turn(role: str, blocks: list) -> bool:
    """Return whether a record holds a typed human message and no tool result."""
    return role == "user" and not any(block.get("type") == "tool_result" for block in blocks)


def _current_turn(records: list) -> list:
    """Return the assistant blocks of every record after the last human message."""
    turn = []
    for record in reversed(records):
        role, blocks = _message(record)
        if _is_human_turn(role, blocks):
            break
        if role == "assistant":
            turn.append(blocks)
    turn.reverse()
    return turn


def _offers_prose_choice(text: str) -> bool:
    """Return whether one message asks a question and names a choice phrase."""
    lowered = text.lower()
    if "?" not in lowered:
        return False
    lines = (line[:MAX_LINE_CHARACTERS] for line in lowered.splitlines())
    return any(phrase in line for line in lines for phrase in CHOICE_PHRASES)


def needs_question_tool(records: list) -> bool:
    """Return whether the current turn offers prose choices without a question call."""
    turn = _current_turn(records)
    blocks = [block for record_blocks in turn for block in record_blocks]
    if any(block.get("type") == "tool_use" and block.get("name") in QUESTION_TOOLS
           for block in blocks):
        return False
    texts = [block.get("text") for record_blocks in turn for block in record_blocks
             if block.get("type") == "text" and isinstance(block.get("text"), str)]
    return bool(texts) and _offers_prose_choice(texts[-1])


def main() -> int:
    """Read one Stop payload and block the stop once when prose offers choices."""
    payload = core.read_payload()
    if not isinstance(payload, dict) or payload.get("stop_hook_active") is True:
        return 0
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return 0
    try:
        records = _records(_read_tail(path))
    except (OSError, ValueError) as error:
        print(f"{GATE}: transcript unreadable ({type(error).__name__}). The check "
              "passed without a scan.", file=sys.stderr)
        return 0
    if needs_question_tool(records):
        print(json.dumps({"decision": "block", "reason": BLOCK_REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
