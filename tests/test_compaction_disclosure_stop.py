#!/usr/bin/env python3
"""Pin the Claude Stop check for the compaction disclosure block (issue 101).

Claude records each compaction as a `compact_boundary` system entry in the
session transcript. The next entry with `isCompactSummary` holds the
compaction text. After the last boundary, some assistant text must hold a
closed fenced block. Its first line is `COMPACTION TEXT DISCLOSURE`. Its body
must contain the compaction text. A missing or wrong block blocks one turn
end. An unreadable transcript, a missing summary entry, or a transcript past
the scan cap lets the turn end with a notice.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "reinject_agents_policy.py"
HOOK_TIMEOUT_SECONDS = 30
DISCLOSURE_MARKER = "COMPACTION TEXT DISCLOSURE"
FENCE = "```"
LONG_FENCE = "````"
SUMMARY_TEXT = "Summary:\n1. Primary request.\n   Fix the gate."
FENCED_SUMMARY_TEXT = "Summary:\n```python\nprint(1)\n```\nDone."
BOUNDARY = {"type": "system", "subtype": "compact_boundary",
            "content": "Conversation compacted"}
SMALL_CHUNK_BYTES = 256
SMALL_SCAN_CAP_BYTES = 1024
# Each padding entry stays well under one small chunk.
PADDING_TEXT_CHARS = 40


def load_hook():
    """Load the reinjection hook by path under a private module name."""
    spec = importlib.util.spec_from_file_location("reinject_stop_check", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(text: str = SUMMARY_TEXT) -> dict:
    """Return the compaction summary entry Claude writes after a boundary."""
    return {"type": "user", "isCompactSummary": True,
            "message": {"role": "user", "content": text}}


def assistant(text: str) -> dict:
    """Return one assistant transcript entry holding `text`."""
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


def disclosure(body: str = SUMMARY_TEXT, fence: str = FENCE,
               closing: str = None) -> dict:
    """Return an assistant entry that carries one disclosure block."""
    closing_fence = fence if closing is None else closing
    lines = ["Disclosure follows.", fence, DISCLOSURE_MARKER, body]
    if closing_fence:
        lines.append(closing_fence)
    return assistant("\n".join(lines))


def run_stop(transcript_path: str, stop_hook_active: bool = False) -> dict:
    """Send one Claude Stop event and return the parsed stdout object."""
    environment = dict(os.environ)
    environment["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
    payload = {"hook_event_name": "Stop", "cwd": str(REPO_ROOT),
               "transcript_path": transcript_path,
               "stop_hook_active": stop_hook_active}
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--client", "claude"],
        input=json.dumps(payload), capture_output=True, text=True,
        env=environment, check=False, timeout=HOOK_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise AssertionError(f"hook failed: {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


class TranscriptCase(unittest.TestCase):
    """Write temporary JSONL transcripts for one test."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def transcript(self, entries: list, prefix: str = "") -> str:
        """Write `entries` as JSONL after `prefix` and return the file path."""
        path = self.directory / "session.jsonl"
        lines = [json.dumps(entry) for entry in entries]
        path.write_text(prefix + "\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def assertBlocks(self, output: dict):
        """Assert one Stop output blocks the turn end."""
        self.assertEqual(output.get("decision"), "block")
        self.assertIn(DISCLOSURE_MARKER, output.get("reason", ""))

    def assertPasses(self, output: dict):
        """Assert one Stop output lets the turn end."""
        self.assertNotEqual(output.get("decision"), "block")


class CompactionDisclosureStopTest(TranscriptCase):
    """The Stop check compares the disclosure with the compaction summary."""

    def test_session_without_compaction_passes(self):
        self.assertPasses(run_stop(self.transcript([assistant("plain reply")])))

    def test_matching_disclosure_passes(self):
        path = self.transcript([BOUNDARY, summary(), disclosure(), assistant("next")])
        self.assertPasses(run_stop(path))

    def test_disclosure_matches_across_whitespace_changes(self):
        reflowed = " ".join(SUMMARY_TEXT.split())
        path = self.transcript([BOUNDARY, summary(), disclosure(body=reflowed)])
        self.assertPasses(run_stop(path))

    def test_missing_disclosure_blocks(self):
        path = self.transcript([BOUNDARY, summary(), assistant("resumed")])
        self.assertBlocks(run_stop(path))

    def test_marker_outside_a_fence_blocks(self):
        path = self.transcript([BOUNDARY, summary(),
                                assistant(DISCLOSURE_MARKER + "\n" + SUMMARY_TEXT)])
        self.assertBlocks(run_stop(path))

    def test_fabricated_body_blocks(self):
        path = self.transcript([BOUNDARY, summary(), disclosure(body="x")])
        self.assertBlocks(run_stop(path))

    def test_partial_body_blocks(self):
        partial = SUMMARY_TEXT.splitlines()[0]
        path = self.transcript([BOUNDARY, summary(), disclosure(body=partial)])
        self.assertBlocks(run_stop(path))

    def test_unclosed_fence_blocks(self):
        path = self.transcript([BOUNDARY, summary(), disclosure(closing="")])
        self.assertBlocks(run_stop(path))

    def test_longer_fence_holds_inner_fences(self):
        path = self.transcript([BOUNDARY, summary(FENCED_SUMMARY_TEXT),
                                disclosure(body=FENCED_SUMMARY_TEXT, fence=LONG_FENCE)])
        self.assertPasses(run_stop(path))

    def test_inner_fence_closes_short_fence(self):
        path = self.transcript([BOUNDARY, summary(FENCED_SUMMARY_TEXT),
                                disclosure(body=FENCED_SUMMARY_TEXT)])
        self.assertBlocks(run_stop(path))

    def test_active_stop_hook_passes(self):
        path = self.transcript([BOUNDARY, summary(), assistant("resumed")])
        self.assertPasses(run_stop(path, stop_hook_active=True))

    def test_newer_boundary_needs_disclosure(self):
        path = self.transcript([BOUNDARY, summary(), disclosure(),
                                BOUNDARY, summary("Second summary."), assistant("resumed")])
        self.assertBlocks(run_stop(path))

    def test_missing_summary_passes_with_notice(self):
        output = run_stop(self.transcript([BOUNDARY, assistant("resumed")]))
        self.assertPasses(output)
        self.assertIn("systemMessage", output)

    def test_missing_transcript_passes_with_notice(self):
        output = run_stop(str(self.directory / "absent.jsonl"))
        self.assertPasses(output)
        self.assertIn("systemMessage", output)

    def test_unreadable_transcript_passes_with_notice(self):
        output = run_stop(str(self.directory))
        self.assertPasses(output)
        self.assertIn("systemMessage", output)


class TranscriptScanTest(TranscriptCase):
    """The reader scans backward past one chunk up to the scan cap."""

    def setUp(self):
        super().setUp()
        self.hook = load_hook()
        for name, value in (("TRANSCRIPT_CHUNK_BYTES", SMALL_CHUNK_BYTES),
                            ("MAX_TRANSCRIPT_SCAN_BYTES", SMALL_SCAN_CAP_BYTES)):
            limit = patch.object(self.hook, name, value)
            limit.start()
            self.addCleanup(limit.stop)

    def padding(self, byte_count: int) -> list:
        """Return assistant entries that fill at least `byte_count` bytes."""
        entry = assistant("y" * PADDING_TEXT_CHARS)
        entry_count = byte_count // len(json.dumps(entry)) + 1
        return [entry] * entry_count

    def test_boundary_past_chunk_blocks(self):
        entries = [BOUNDARY, summary(), assistant("resumed")] + self.padding(SMALL_CHUNK_BYTES)
        transcript, complete = self.hook.read_transcript_from_last_boundary(
            self.transcript(entries))
        self.assertTrue(complete)
        self.assertIsNotNone(self.hook.compaction_verdict(transcript))

    def test_boundary_past_cap_incomplete(self):
        entries = [BOUNDARY, summary()] + self.padding(SMALL_SCAN_CAP_BYTES * 2)
        _transcript, complete = self.hook.read_transcript_from_last_boundary(
            self.transcript(entries))
        self.assertFalse(complete)

    def test_small_file_scans_completely(self):
        transcript, complete = self.hook.read_transcript_from_last_boundary(
            self.transcript([assistant("plain")]))
        self.assertTrue(complete)
        self.assertIsNone(self.hook.compaction_verdict(transcript))


def tool_call_decoy() -> dict:
    """Return an assistant tool call whose argument serializes as the raw token.

    JSON escapes quotes inside message text. A string value of its own
    serializes with bare quotes, so the raw boundary token appears.
    """
    return {"type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Grep",
                 "input": {"pattern": "compact_boundary"}}]}}


class BoundaryTextTest(TranscriptCase):
    """Only a parsed boundary entry counts as a compaction."""

    def test_decoy_holds_the_raw_token(self):
        self.assertIn('"compact_boundary"', json.dumps(tool_call_decoy()))

    def test_later_tool_call_token_blocks(self):
        path = self.transcript([BOUNDARY, summary(), assistant("resumed"),
                                tool_call_decoy()])
        self.assertBlocks(run_stop(path))

    def test_token_without_boundary_passes(self):
        self.assertPasses(run_stop(self.transcript([tool_call_decoy()])))


class BoundaryLineTest(unittest.TestCase):
    """Direct calls cover each boundary line outcome."""

    @classmethod
    def setUpClass(cls):
        cls.hook = load_hook()
        cls.boundary_line = json.dumps(BOUNDARY).encode("utf-8")

    def test_invalid_json_line_skipped(self):
        data = self.boundary_line + b'\n"compact_boundary" broken\n'
        self.assertEqual(self.hook._boundary_line_start(data, 0), 0)

    def test_invalid_utf8_line_skipped(self):
        self.assertFalse(self.hook._is_boundary_line(b'"compact_boundary" \xff'))

    def test_final_line_without_newline(self):
        data = b"{}\n" + self.boundary_line
        self.assertEqual(self.hook._boundary_line_start(data, 0), len(b"{}\n"))

    def test_partial_line_requests_more(self):
        self.assertEqual(self.hook._boundary_line_start(self.boundary_line, 1),
                         self.hook.NEED_EARLIER_BYTES)

    def test_no_token_no_boundary(self):
        self.assertEqual(self.hook._boundary_line_start(b"{}\n", 0),
                         self.hook.NO_BOUNDARY)


class StopHelperTest(TranscriptCase):
    """In-process calls reach each Stop helper branch."""

    def setUp(self):
        super().setUp()
        self.hook = load_hook()

    def test_open_rejects_missing_path(self):
        for value in (None, ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.hook._open_transcript(value)

    def test_open_rejects_a_directory(self):
        with self.assertRaises((OSError, ValueError)):
            self.hook._open_transcript(str(self.directory))

    def test_open_rejects_a_device(self):
        # The null device opens like a file but is not a regular file.
        with self.assertRaises(ValueError):
            self.hook._open_transcript(os.devnull)

    def test_parser_skips_malformed_lines(self):
        transcript = "\n".join(("not json", "[1, 2]", json.dumps(BOUNDARY),
                                json.dumps(summary()), json.dumps(assistant("resumed"))))
        summary_text, texts = self.hook._entries_after_last_compaction(transcript)
        self.assertEqual(summary_text, SUMMARY_TEXT)
        self.assertEqual(texts, ["resumed"])

    def test_entry_text_reads_string_content(self):
        entry = {"type": "assistant", "message": {"role": "assistant", "content": "plain"}}
        self.assertEqual(self.hook._entry_text(entry), "plain")

    def test_entry_text_rejects_other_content(self):
        entry = {"type": "assistant", "message": {"role": "assistant", "content": 5}}
        self.assertEqual(self.hook._entry_text(entry), "")

    def test_incomplete_scan_notice(self):
        path = self.transcript([assistant("plain")])
        printed = []
        with patch.object(self.hook, "read_transcript_from_last_boundary",
                          return_value=("", False)), \
                patch("builtins.print", side_effect=printed.append):
            code = self.hook.emit_claude_stop({"transcript_path": path})
        self.assertEqual(code, 0)
        output = json.loads(printed[0])
        self.assertIn("systemMessage", output)
        self.assertNotIn("decision", output)


class StopRegistrationTest(unittest.TestCase):
    """Both Claude settings files run the reinjection hook on Stop."""

    def test_stop_check_registered(self):
        for relative_path in (".claude/settings.json",
                              "hooks/claude-code-settings.example.json"):
            with self.subTest(path=relative_path):
                settings = json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
                commands = [
                    " ".join(hook.get("args", []))
                    for group in settings["hooks"]["Stop"]
                    for hook in group["hooks"]]
                self.assertTrue(any("reinject_agents_policy.py --client claude" in command
                                    for command in commands))


if __name__ == "__main__":
    unittest.main()
