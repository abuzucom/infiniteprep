#!/usr/bin/env python3
"""The advisory Stop hook blocks once when a turn offers choices in prose only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPOSITORY_ROOT / "hooks" / "detect_prose_options.py"
HOOK_TIMEOUT_SECONDS = 15
PROSE_OPTIONS = "I can fix it two ways. Option A rewrites the parser. Which option do you prefer?"
PLAIN_PROSE = "The fix is in place. All tests pass."
PADDING_RECORDS = 4000


def user_record(text: str) -> dict:
    """Return a genuine user message record."""
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result_record() -> dict:
    """Return a tool result record, which does not start a turn."""
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def assistant_record(text: str = "", tool: str = "") -> dict:
    """Return an assistant record with optional text and an optional tool call."""
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "id": "t1", "name": tool, "input": {}})
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


class ProseOptionsTest(unittest.TestCase):
    """Prose choices without a question tool call in the same turn block the stop."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def transcript(self, records: list) -> Path:
        path = self.directory / "transcript.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records),
                        encoding="utf-8")
        return path

    def run_stop(self, path, active: bool = False):
        payload = {"hook_event_name": "Stop", "stop_hook_active": active,
                   "transcript_path": str(path), "cwd": str(REPOSITORY_ROOT)}
        result = subprocess.run([sys.executable, str(HOOK_PATH)], input=json.dumps(payload),
                                text=True, capture_output=True, timeout=HOOK_TIMEOUT_SECONDS,
                                check=False, cwd=REPOSITORY_ROOT)
        parsed = json.loads(result.stdout) if result.stdout.strip() else {}
        return result.returncode, parsed, result.stderr

    def test_prose_options_block(self) -> None:
        path = self.transcript([user_record("fix the parser"), assistant_record(PROSE_OPTIONS)])
        code, parsed, _ = self.run_stop(path)
        self.assertEqual(code, 0)
        self.assertEqual(parsed.get("decision"), "block")
        self.assertIn("AskUserQuestion", parsed["reason"])

    def test_question_call_in_turn_passes(self) -> None:
        path = self.transcript([user_record("fix the parser"),
                                assistant_record(tool="AskUserQuestion"), tool_result_record(),
                                assistant_record(PROSE_OPTIONS)])
        self.assertEqual(self.run_stop(path)[1], {})

    def test_earlier_turn_question_blocks(self) -> None:
        path = self.transcript([user_record("first"), assistant_record(tool="AskUserQuestion"),
                                tool_result_record(), user_record("second"),
                                assistant_record(PROSE_OPTIONS)])
        self.assertEqual(self.run_stop(path)[1].get("decision"), "block")

    def test_plain_prose_passes(self) -> None:
        path = self.transcript([user_record("go"), assistant_record(PLAIN_PROSE)])
        self.assertEqual(self.run_stop(path)[1], {})

    def test_phrase_without_question_mark_passes(self) -> None:
        path = self.transcript([user_record("go"),
                                assistant_record("Option A is in place. Tests pass.")])
        self.assertEqual(self.run_stop(path)[1], {})

    def test_active_stop_hook_passes(self) -> None:
        path = self.transcript([user_record("go"), assistant_record(PROSE_OPTIONS)])
        self.assertEqual(self.run_stop(path, active=True)[1], {})

    def test_missing_transcript_fails_open(self) -> None:
        code, parsed, stderr = self.run_stop(self.directory / "absent.jsonl")
        self.assertEqual((code, parsed), (0, {}))
        self.assertIn("detect_prose_options", stderr)

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_linked_transcript_fails_open(self) -> None:
        real = self.transcript([user_record("go"), assistant_record(PROSE_OPTIONS)])
        link = self.directory / "link.jsonl"
        link.symlink_to(real)
        code, parsed, _ = self.run_stop(link)
        self.assertEqual((code, parsed), (0, {}))

    def test_large_transcript_reads_tail(self) -> None:
        padding = [assistant_record("x" * 200) for _ in range(PADDING_RECORDS)]
        path = self.transcript([*padding, user_record("go"), assistant_record(PROSE_OPTIONS)])
        self.assertEqual(self.run_stop(path)[1].get("decision"), "block")

    def test_odd_records_are_skipped(self) -> None:
        path = self.directory / "transcript.jsonl"
        lines = [json.dumps(user_record("go")), "not json", json.dumps(["a list"]),
                 json.dumps({"message": "text"}),
                 json.dumps({"message": {"role": "assistant", "content": 7}}),
                 json.dumps(assistant_record(PROSE_OPTIONS))]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(self.run_stop(path)[1].get("decision"), "block")

    def test_directory_transcript_fails_open(self) -> None:
        code, parsed, stderr = self.run_stop(self.directory)
        self.assertEqual((code, parsed), (0, {}))
        self.assertIn("detect_prose_options", stderr)

    def test_missing_transcript_path_passes(self) -> None:
        payload = json.dumps({"hook_event_name": "Stop", "stop_hook_active": False})
        result = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True,
                                capture_output=True, timeout=HOOK_TIMEOUT_SECONDS, check=False)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, ""))

    def test_unreadable_payload_fails_open(self) -> None:
        result = subprocess.run([sys.executable, str(HOOK_PATH)], input="not json", text=True,
                                capture_output=True, timeout=HOOK_TIMEOUT_SECONDS, check=False)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, ""))


if __name__ == "__main__":
    unittest.main()
