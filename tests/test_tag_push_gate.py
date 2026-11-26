#!/usr/bin/env python3
"""Check that the shell gates deny tag pushes and local tag writes."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))
import _gate_core

BLOCKING_EXIT_CODE = 2
TIMEOUT_SECONDS = 30
SHELL_HOOKS = (
    ("block_destructive_bash.py", "Bash"),
    ("block_destructive_powershell.py", "PowerShell"),
    ("block_destructive_cmd.py", "Cmd"),
)
DENIED_PUSHES = (
    "git push --tags",
    "git push origin --tags",
    "git push --follow-tags origin feat/x",
    "git push origin refs/tags/v2.27.0",
    "git push origin HEAD:refs/tags/v1",
    "git push origin v2.27.0",
    "git push origin +v2.27.0",
    "git push origin feat/x:v3.0.0",
    "git push --force --tags",
)
ALLOWED_PUSHES = (
    "git push origin feat/x",
    "git push -u origin feat/add-tagged-release-workflow",
    "git push origin feat/v2-release-notes",
    "git push origin HEAD:refs/heads/feat/x",
)
DENIED_TAG_WRITES = (
    "git tag v2.27.0",
    "git tag v2.27.0 HEAD",
    "git tag -a v2.27.0 -m release",
    "git tag -s v2.27.0",
    "git tag -f v2.27.0",
    "git tag -d v2.27.0",
    "git tag --delete v2.27.0",
    "git tag -am release v2.27.0",
)
ALLOWED_TAG_READS = (
    "git tag",
    "git tag -l",
    "git tag --list",
    "git tag -l v2.*",
    "git tag --list --sort=-v:refname",
    "git tag -n5",
    "git tag --contains HEAD",
    "git tag --points-at HEAD",
    "git tag --merged main --sort -creatordate",
    "git tag -v v2.27.0",
    "git tag --format=%(refname) -l",
)


def git_decision(command: str) -> str:
    """Return the shared core decision word for one git command."""
    return _gate_core.git_verdict(command.split()[1:])[0]


def run_shell_hook(hook_name: str, tool: str, command: str) -> subprocess.CompletedProcess:
    """Send one command payload to a shell gate without running the command."""
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
               "permission_mode": "default", "tool_input": {"command": command}}
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "hooks" / hook_name)],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=TIMEOUT_SECONDS, check=False)


class TagPushVerdictTest(unittest.TestCase):
    def test_tag_pushes_deny(self):
        for command in DENIED_PUSHES:
            with self.subTest(command=command):
                self.assertEqual(git_decision(command), "deny")

    def test_tag_push_reason(self):
        decision, reason = _gate_core.git_verdict(["push", "--tags"])
        self.assertEqual(decision, "deny")
        self.assertIn("tag", reason)

    def test_branch_pushes_keep_their_verdict(self):
        for command in ALLOWED_PUSHES:
            with self.subTest(command=command):
                self.assertEqual(git_decision(command), "")

    def test_force_push_without_tags_asks(self):
        self.assertEqual(git_decision("git push --force origin feat/x"), "ask")


class TagSubcommandVerdictTest(unittest.TestCase):
    def test_tag_writes_deny(self):
        for command in DENIED_TAG_WRITES:
            with self.subTest(command=command):
                self.assertEqual(git_decision(command), "deny")

    def test_tag_reads_pass(self):
        for command in ALLOWED_TAG_READS:
            with self.subTest(command=command):
                self.assertEqual(git_decision(command), "")


class ShellGateTagParityTest(unittest.TestCase):
    def test_shell_gates_block_tag_push(self):
        for hook_name, tool in SHELL_HOOKS:
            for command in ("git push origin --tags", "git tag -a v2.27.0 -m x"):
                with self.subTest(hook=hook_name, command=command):
                    result = run_shell_hook(hook_name, tool, command)
                    self.assertEqual(result.returncode, BLOCKING_EXIT_CODE, result.stdout)
                    self.assertIn("tag", result.stderr)

    def test_shell_gates_pass_tag_listing(self):
        for hook_name, tool in SHELL_HOOKS:
            with self.subTest(hook=hook_name):
                result = run_shell_hook(hook_name, tool, "git tag -l")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
