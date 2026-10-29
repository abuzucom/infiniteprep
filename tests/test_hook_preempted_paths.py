#!/usr/bin/env python3
"""Pin the outcomes that earlier checks deliver for removed hook statements.

Each removed statement sat behind an earlier check that already handled its
input. These cases run the real hook code on those inputs, so a later change
to the earlier check cannot silently drop the outcome.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"
BLOCKING_EXIT_CODE = 2
HOOK_TIMEOUT_SECONDS = 30


def cmd_decision(command: str) -> str:
    """Return allow, ask, or deny from the CMD gate for `command`."""
    payload = {
        "hook_event_name": "PreToolUse",
        "permission_mode": "default",
        "tool_name": "Cmd",
        "tool_input": {"command": command},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "block_destructive_cmd.py")],
        input=json.dumps(payload), cwd=REPO_ROOT, capture_output=True,
        text=True, timeout=HOOK_TIMEOUT_SECONDS, check=False,
    )
    if result.returncode == BLOCKING_EXIT_CODE:
        return "deny"
    if not result.stdout.strip():
        return "allow"
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


def load_gate_adoption_hook():
    """Import hooks/enforce_gate_adoption.py from its file path."""
    spec = importlib.util.spec_from_file_location(
        "enforce_gate_adoption_preempted", HOOKS / "enforce_gate_adoption.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HOOKS))
    spec.loader.exec_module(module)
    return module


class CmdStorageDestructionTest(unittest.TestCase):
    """The prohibited-command check denies partition and format programs."""

    def test_diskpart_is_denied(self) -> None:
        self.assertEqual(cmd_decision("diskpart"), "deny")

    def test_format_is_denied(self) -> None:
        self.assertEqual(cmd_decision("format c: /q"), "deny")


class StagingDirectoryWriteTest(unittest.TestCase):
    """Only paths below the staging directory count as staging writes."""

    def test_staging_directory_itself_is_not_a_staging_write(self) -> None:
        hook = load_gate_adoption_hook()
        staging = str(REPO_ROOT / hook.STAGING_DIRECTORY)
        self.assertFalse(hook._staging_write("Write", {"file_path": staging}))

    def test_file_below_staging_directory_is_a_staging_write(self) -> None:
        hook = load_gate_adoption_hook()
        target = str(REPO_ROOT / hook.STAGING_DIRECTORY / "candidate" / "a.py")
        self.assertTrue(hook._staging_write("Write", {"file_path": target}))


class GateFileConsentReasonTest(unittest.TestCase):
    """A write under hooks/ asks with the Rule 22 instructions."""

    def test_hook_file_write_cites_rule_22(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            target = os.path.join(project, "hooks", "new_gate.py")
            payload = {"hook_event_name": "PreToolUse", "permission_mode": "default",
                       "tool_name": "Write",
                       "tool_input": {"file_path": target, "content": "x"}}
            environment = dict(os.environ, CLAUDE_PROJECT_DIR=project)
            result = subprocess.run(
                [sys.executable, str(HOOKS / "require_consent.py")],
                input=json.dumps(payload), env=environment, capture_output=True,
                text=True, timeout=HOOK_TIMEOUT_SECONDS, check=False,
            )
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "ask")
        self.assertIn("Rule 22", output["permissionDecisionReason"])


class PowerShellAmbiguousPathTest(unittest.TestCase):
    """A write target that hides its value behind an expansion asks."""

    def test_expansion_in_write_target_asks(self) -> None:
        sys.path.insert(0, str(HOOKS))
        import _gate_core
        for name, args in (("set-content", ["-Path", "$target", "-Value", "x"]),
                           ("out-file", ["-FilePath", "$env:TEMP/x"])):
            with self.subTest(name=name):
                self.assertEqual(
                    _gate_core._powershell_path_policy(name, args, []),
                    ("ask", "a file path contains an expansion the gate cannot resolve"))


class ClaudeCopyLineEndingTest(unittest.TestCase):
    """The Claude copy check compares both sides after full CRLF folding."""

    def test_copy_matching_twice_folded_policy_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            # One CRLF pass turns CR CR LF into CR LF, so a second pass on
            # the policy side still changes the text.
            agents = b"policy\r\r\nbody\n"
            (root / "AGENTS.md").write_bytes(agents)
            supporting_names = (
                "docs/agent-policy/adoption.md", "docs/agent-policy/enforcement.md",
                "docs/agent-policy/clients.md", "docs/agent-policy/github.md",
                "docs/agent-policy/security.md", "docs/project-orientation.md")
            for relative_name in supporting_names:
                path = root / relative_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"detail\n")
            assembled = agents + b"detail\n" * len(supporting_names)
            folded = assembled.decode("ascii").replace("\r\n", "\n")
            (root / "CLAUDE.md").write_bytes(
                folded.replace("\r\n", "\n").encode("ascii"))
            # The hook accepts only the root it is installed in.
            installed_hook = root / "hooks" / "reinject_agents_policy.py"
            installed_hook.parent.mkdir()
            installed_hook.write_bytes(
                (HOOKS / "reinject_agents_policy.py").read_bytes())
            payload = {"hook_event_name": "SessionStart", "source": "startup",
                       "cwd": str(root)}
            environment = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
            result = subprocess.run(
                [sys.executable, str(installed_hook), "--client", "claude"],
                input=json.dumps(payload), env=environment, capture_output=True,
                text=True, timeout=HOOK_TIMEOUT_SECONDS, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("not synchronized", result.stderr)


if __name__ == "__main__":
    unittest.main()
