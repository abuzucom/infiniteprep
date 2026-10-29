#!/usr/bin/env python3
"""Regression tests for hook checks that never fired or contradicted each other.

Each case runs the real hook entrypoint as a subprocess against a synthetic
payload, except the guard-order case, which counts calls into the shared
core from the command execution gate.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.read_only_branch_support import DETACHED_HEAD, BranchFixture, decision

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"
BLOCKING_EXIT_CODE = 2
HOOK_TIMEOUT_SECONDS = 30
REBASE_RECOVERY_TEXT = "git rebase --abort"
BRANCH_RECOVERY_COMMANDS = ("git switch -c", "git branch -m")


def run_gate(hook: str, tool_name: str, command: str) -> str:
    """Return allow, ask, or deny from one shell gate for `command`."""
    payload = {
        "hook_event_name": "PreToolUse",
        "permission_mode": "default",
        "tool_name": tool_name,
        "tool_input": {"command": command},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / hook)], input=json.dumps(payload),
        cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=HOOK_TIMEOUT_SECONDS, check=False,
    )
    if result.returncode == BLOCKING_EXIT_CODE:
        return "deny"
    if not result.stdout.strip():
        return "allow"
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


def bash(command: str) -> str:
    """Return the Bash gate decision for `command`."""
    return run_gate("block_destructive_bash.py", "Bash", command)


def powershell(command: str) -> str:
    """Return the PowerShell gate decision for `command`."""
    return run_gate("block_destructive_powershell.py", "PowerShell", command)


def cmd(command: str) -> str:
    """Return the CMD gate decision for `command`."""
    return run_gate("block_destructive_cmd.py", "Cmd", command)


class NeverFiringCheckTest(unittest.TestCase):
    """Checks whose markers never matched now ask."""

    def test_auditctl_delete_all_rules_asks(self) -> None:
        self.assertEqual(bash("auditctl -D"), "ask")

    def test_in_place_edit_over_a_glob_asks(self) -> None:
        for command in ("sed -n -i 's/a/b/p' *.py", "sed -ni 's/a/b/p' *.py",
                        "sed -i.bak 's/a/b/' *.py", "sed --in-place 's/a/b/' *.py",
                        "sed --in-place=.bak 's/a/b/' *.py", "sed -Ei 's/a/b/' *.py",
                        "perl -n -i -e 'print' *.py", "perl -pi -e 's/a/b/' *.py",
                        "perl -i.bak -pe 's/a/b/' *.py"):
            with self.subTest(command=command):
                self.assertEqual(bash(command), "ask")

    def test_xargs_max_args_is_not_a_dry_run(self) -> None:
        self.assertEqual(bash("find . -name x | xargs -n 1 rm"), "ask")

    def test_cmd_git_clean_dry_run_stays_allowed(self) -> None:
        self.assertEqual(cmd("git clean -n"), "allow")

    def test_reads_and_dry_runs_stay_allowed(self) -> None:
        for command in ("sed -n 's/a/b/p' *.py", "sed -e 's/i/j/' *.py",
                        "perl -Mstrict -e 'print' *.py", "git clean -n"):
            with self.subTest(command=command):
                self.assertEqual(bash(command), "allow")


class VolumeVerdictTest(unittest.TestCase):
    """A recursive mode change under a mount point asks in every shell."""

    def test_bash_recursive_mode_change_under_a_volume_asks(self) -> None:
        for command in ("chmod -R 777 /mnt/x", "chown -R nobody /media/disk",
                        "chgrp --recursive staff /var/tmp/x"):
            with self.subTest(command=command):
                self.assertEqual(bash(command), "ask")

    def test_powershell_recursive_mode_change_under_a_volume_asks(self) -> None:
        self.assertEqual(powershell("chmod -R 777 /mnt/x"), "ask")

    def test_cmd_recursive_mode_change_under_a_volume_asks(self) -> None:
        for command in ("icacls /mnt/x /grant everyone:F /T", "takeown /F /mnt/x /R"):
            with self.subTest(command=command):
                self.assertEqual(cmd(command), "ask")

    def test_non_recursive_or_project_mode_change_stays_allowed(self) -> None:
        for command in ("chmod 644 /mnt/x/file", "chmod -R 755 build",
                        "chmod -r /mnt/x/file"):
            with self.subTest(command=command):
                self.assertEqual(bash(command), "allow")


class RebaseRecoveryMessageTest(unittest.TestCase):
    """Lifecycle messages during a rebase name only the rebase commands."""

    def fixture(self) -> BranchFixture:
        """Return a detached fixture with an active rebase."""
        branch_fixture = BranchFixture(self, DETACHED_HEAD)
        (branch_fixture.admin / "rebase-merge").mkdir()
        return branch_fixture

    def assert_rebase_only(self, text: str) -> None:
        """Assert `text` names rebase recovery and no branch recovery."""
        self.assertIn(REBASE_RECOVERY_TEXT, text)
        for command in BRANCH_RECOVERY_COMMANDS:
            self.assertNotIn(command, text)

    def test_stop_names_only_rebase_recovery(self) -> None:
        result = self.fixture().lifecycle("Stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_rebase_only(json.loads(result.stdout)["reason"])

    def test_context_events_name_only_rebase_recovery(self) -> None:
        branch_fixture = self.fixture()
        for event in ("SessionStart", "UserPromptSubmit"):
            with self.subTest(event=event):
                result = branch_fixture.lifecycle(event)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)["hookSpecificOutput"]
                self.assert_rebase_only(output["additionalContext"])


class UnreadableGitdirLifecycleTest(unittest.TestCase):
    """A broken gitdir pointer still reports the branch lookup failure."""

    def test_stop_names_the_branch_lookup_failure(self) -> None:
        branch_fixture = BranchFixture(self, "main")
        branch_fixture.admin.rename(branch_fixture.root / "moved-admin")
        branch_fixture.admin.write_text("not a gitdir pointer\n", encoding="utf-8")
        result = branch_fixture.lifecycle("Stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("branch lookup failed", output["reason"])


class ProhibitedBranchSwitchTest(unittest.TestCase):
    """Switching to an existing claude/ branch is not a read-only act."""

    def test_switch_to_existing_claude_branch_is_denied(self) -> None:
        for state in ("main", DETACHED_HEAD):
            with self.subTest(state=state):
                branch_fixture = BranchFixture(self, state)
                branch_fixture.add_local_branch("claude/x")
                self.assertEqual(
                    decision(branch_fixture.shell("git switch claude/x")), "deny")


class OutsideRootReasonTest(unittest.TestCase):
    """A new non-test file outside the root asks without a test-file reason."""

    def test_new_file_outside_root_asks_with_an_accurate_reason(self) -> None:
        with tempfile.TemporaryDirectory() as project, \
                tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "notes.txt")
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
        self.assertNotIn("test file", output["permissionDecisionReason"])
        self.assertIn("outside the project root", output["permissionDecisionReason"])


class GuardOrderTest(unittest.TestCase):
    """The trusted wrapper length guard runs before the forge verdict."""

    def test_bare_run_skips_the_forge_verdict(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "command_execution_gate_order", HOOKS / "_command_execution_gate.py")
        gate = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(HOOKS))
        spec.loader.exec_module(gate)
        tokens = ["python", "scripts/trusted_gh.py", "run"]
        with mock.patch.object(gate.core, "forge_verdict",
                               return_value=("", "")) as forge:
            self.assertFalse(gate._python_workflow(tokens, str(REPO_ROOT)))
        self.assertEqual(forge.call_count, 0)


if __name__ == "__main__":
    unittest.main()
