#!/usr/bin/env python3
"""Tests for the required disclosure form in the compaction directive."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "reinject_agents_policy.py"
DISCLOSURE_MARKER = "COMPACTION TEXT DISCLOSURE"
REQUIRED_PHRASES = (
    "Reproduce it verbatim inside a fenced block",
    "The client interface hides the compaction message",
    "A reference to text above, a paraphrase, or a summary is not",
)


def run_compact_session(client: str) -> str:
    """Run one client adapter for a compaction session and return context."""
    environment = dict(os.environ)
    environment["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
    payload = {
        "hook_event_name": "SessionStart",
        "source": "compact",
        "cwd": str(REPO_ROOT),
    }
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--client", client],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"{client} hook failed: {result.stderr}")
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


class CompactionDisclosureFormTest(unittest.TestCase):
    """Compaction context defines a verbatim, marker-led disclosure."""

    def assert_disclosure_form(self, context: str) -> None:
        """Check the marker and disclosure wording in one context."""
        directive = context.split("MANDATORY AGENTS.md RE-ADOPTION")[0]
        self.assertIn(DISCLOSURE_MARKER, directive)
        for phrase in REQUIRED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, " ".join(directive.split()))

    def test_codex_compaction_requires_verbatim_disclosure(self):
        self.assert_disclosure_form(run_compact_session("codex"))

    def test_claude_compaction_requires_verbatim_disclosure(self):
        self.assert_disclosure_form(run_compact_session("claude"))


if __name__ == "__main__":
    unittest.main()
