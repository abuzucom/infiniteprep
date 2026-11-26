#!/usr/bin/env python3
"""Both Claude settings files register the question, prose, and MCP write hooks."""
import json
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILES = (REPOSITORY_ROOT / ".claude" / "settings.json",
                  REPOSITORY_ROOT / "hooks" / "claude-code-settings.example.json")
EXPECTED = (("enforce_single_question.py", "PreToolUse", "AskUserQuestion"),
            ("deny_mcp_github_writes.py", "PreToolUse", "mcp__.*"),
            ("detect_prose_options.py", "Stop", ""))


def registered_matchers(settings: dict, event: str, hook: str) -> set:
    """Return every matcher under one event that runs the named hook."""
    matchers = set()
    for group in settings.get("hooks", {}).get(event, []):
        for entry in group.get("hooks", []):
            parts = [entry.get("command", ""), *entry.get("args", [])]
            if any(part.endswith(f"/hooks/{hook}") for part in parts):
                matchers.add(group.get("matcher", ""))
    return matchers


class QuestionHookWiringTest(unittest.TestCase):
    """A hook under the wrong event or matcher never sees the call it gates."""

    def test_hooks_registered(self) -> None:
        for path in SETTINGS_FILES:
            settings = json.loads(path.read_text(encoding="utf-8"))
            for hook, event, matcher in EXPECTED:
                with self.subTest(path=path.name, hook=hook):
                    self.assertEqual(registered_matchers(settings, event, hook), {matcher})


if __name__ == "__main__":
    unittest.main()
