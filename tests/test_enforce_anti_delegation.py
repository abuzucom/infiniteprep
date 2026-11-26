#!/usr/bin/env python3
"""Test the anti-delegation hook and its client registrations."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "enforce_anti_delegation.py"
BLOCKING_EXIT_CODE = 2
DELEGATING_TEXT = "Please run git merge --abort to clear the conflict."
CLEAN_TEXT = "The merge safety check passed for the target branch."


def load_hook_module():
    """Load the hook module in process for its defensive branches."""
    spec = importlib.util.spec_from_file_location(
        "enforce_anti_delegation_module", HOOK_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_hook(payload: dict) -> tuple[int, str, str]:
    """Run the hook as a subprocess and return its code, output, and error."""
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--client", "claude"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    return result.returncode, result.stdout, result.stderr


def hook_entries(group: dict) -> list[dict]:
    """Return the hook entries of one group, in either client file shape."""
    entries = group.get("hooks")
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    return [group]


def registered_commands(settings: dict, event: str) -> list[str]:
    """Return every hook command registered under one event."""
    commands = []
    for group in event_groups(settings, event):
        for entry in hook_entries(group):
            parts = [entry.get("command", "")] + list(entry.get("args", []))
            commands.append(" ".join(part for part in parts if part))
    return commands


def event_groups(document: object, event: str) -> list[dict]:
    """Return every hook group named by one event in a nested client file."""
    groups = []
    if isinstance(document, dict):
        for key, value in document.items():
            if key == event and isinstance(value, list):
                groups.extend(item for item in value if isinstance(item, dict))
            groups.extend(event_groups(value, event))
    elif isinstance(document, list):
        for value in document:
            groups.extend(event_groups(value, event))
    return groups


def registered_names(document: dict, event: str) -> list[str]:
    """Return every hook command registered under one event of a client file."""
    return registered_commands(document, event)


class StopEventTest(unittest.TestCase):
    """Verify that a delegating completion attempt is blocked."""

    def test_delegating_output_blocks_the_stop(self) -> None:
        code, stdout, _error = run_hook({
            "hook_event_name": "Stop",
            "assistant_message": DELEGATING_TEXT,
        })
        self.assertEqual(code, 0)
        decision = json.loads(stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("delegates a recovery command", decision["reason"])

    def test_clean_output_allows_the_stop(self) -> None:
        code, stdout, _error = run_hook({
            "hook_event_name": "Stop",
            "assistant_message": CLEAN_TEXT,
        })
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_active_stop_no_reblock(self) -> None:
        code, stdout, _error = run_hook({
            "hook_event_name": "Stop",
            "stop_hook_active": True,
            "assistant_message": DELEGATING_TEXT,
        })
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_transcript_output_blocks_the_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "transcript.jsonl"
            transcript.write_text(
                json.dumps({"role": "assistant", "content": CLEAN_TEXT}) + "\n"
                + json.dumps({"role": "assistant", "content": DELEGATING_TEXT}) + "\n",
                encoding="utf-8",
            )
            code, stdout, _error = run_hook({
                "hook_event_name": "Stop",
                "transcript_path": str(transcript),
            })
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["decision"], "block")

    def test_unreadable_payload_blocks(self) -> None:
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="this is not json",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, BLOCKING_EXIT_CODE)

    def test_empty_session_start_allows(self) -> None:
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH), "--client", "gemini"],
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class PreToolEventTest(unittest.TestCase):
    """Verify that a delegating payload denies the tool call."""

    def test_delegating_payload_denies_the_tool(self) -> None:
        code, stdout, _error = run_hook({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "permission_mode": "default",
            "assistant_message": DELEGATING_TEXT,
        })
        self.assertEqual(code, BLOCKING_EXIT_CODE)
        decision = json.loads(stdout)
        self.assertEqual(
            decision["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_clean_payload_allows_the_tool(self) -> None:
        code, stdout, _error = run_hook({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "permission_mode": "default",
            "assistant_message": CLEAN_TEXT,
        })
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")


class PayloadShapeTest(unittest.TestCase):
    """Verify the payload and transcript shapes the hook accepts."""

    def test_content_block_list_blocks(self) -> None:
        code, stdout, _error = run_hook({
            "hook_event_name": "Stop",
            "assistant_message": [
                {"type": "text", "text": DELEGATING_TEXT},
                "plain block",
            ],
        })
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["decision"], "block")

    def test_nested_transcript_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "transcript.jsonl"
            transcript.write_text(
                json.dumps({
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": DELEGATING_TEXT}],
                    }
                }) + "\n",
                encoding="utf-8",
            )
            code, stdout, _error = run_hook({
                "hook_event_name": "Stop",
                "transcript_path": str(transcript),
            })
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["decision"], "block")

    def test_no_assistant_record_allows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "transcript.jsonl"
            transcript.write_text(
                json.dumps({"role": "user", "content": "hello"}) + "\n\n",
                encoding="utf-8",
            )
            code, stdout, _error = run_hook({
                "hook_event_name": "Stop",
                "transcript_path": str(transcript),
            })
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_transcript_with_unreadable_records_allows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "transcript.jsonl"
            transcript.write_text(
                "not json\n[]\n" + json.dumps({"role": "user", "content": "hi"}) + "\n",
                encoding="utf-8",
            )
            code, stdout, _error = run_hook({
                "hook_event_name": "Stop",
                "transcript_path": str(transcript),
            })
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_directory_transcript_allows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, stdout, _error = run_hook({
                "hook_event_name": "Stop",
                "transcript_path": directory,
            })
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_session_event_with_output_blocks(self) -> None:
        code, stdout, _error = run_hook({
            "hook_event_name": "SessionStart",
            "assistant_message": DELEGATING_TEXT,
        })
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["decision"], "block")

    def test_missing_checker_no_finding(self) -> None:
        module = load_hook_module()
        module.checker = None
        self.assertEqual(module._findings({"assistant_message": DELEGATING_TEXT}), [])


class RegistrationTest(unittest.TestCase):
    """Verify that every adopted client registers the hook."""

    def test_claude_registers_stop_events(self) -> None:
        for path in (
            REPO_ROOT / ".claude" / "settings.json",
            REPO_ROOT / "hooks" / "claude-code-settings.example.json",
        ):
            settings = json.loads(path.read_text(encoding="utf-8"))
            for event in ("Stop", "SubagentStop"):
                with self.subTest(path=path.name, event=event):
                    commands = registered_commands(settings, event)
                    self.assertTrue(
                        any("enforce_anti_delegation.py" in item for item in commands)
                    )

    def test_codex_registration(self) -> None:
        document = json.loads(
            (REPO_ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8")
        )
        for event in ("SessionStart", "PreToolUse"):
            with self.subTest(event=event):
                commands = registered_names(document, event)
                self.assertTrue(
                    any("enforce_anti_delegation.py" in item for item in commands)
                )

    def test_gemini_registration(self) -> None:
        document = json.loads(
            (REPO_ROOT / ".gemini" / "settings.json").read_text(encoding="utf-8")
        )
        for event in ("SessionStart", "BeforeTool"):
            with self.subTest(event=event):
                commands = registered_names(document, event)
                self.assertTrue(
                    any("enforce_anti_delegation.py" in item for item in commands)
                )

    def test_antigravity_registration(self) -> None:
        document = json.loads(
            (REPO_ROOT / ".agents" / "hooks.json").read_text(encoding="utf-8")
        )
        for event in ("PreInvocation", "PreToolUse"):
            with self.subTest(event=event):
                commands = registered_names(document, event)
                self.assertTrue(
                    any("enforce_anti_delegation.py" in item for item in commands)
                )


if __name__ == "__main__":
    unittest.main()
