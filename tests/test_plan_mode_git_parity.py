#!/usr/bin/env python3
"""Pin the plan-mode Git write denial across the shell gates (issue 100).

Plan mode permits inspection only. Each shell gate denies a Git write while
`permission_mode` is plan and keeps read-only Git commands open. The three
gates share one core helper. The verdicts match across the gates.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_gate_parity import (BASH_HOOK, CMD_HOOK, POWERSHELL_HOOK, _decision,
                                    bash, cmd, powershell)

PLAN_MODE = "plan"
DEFAULT_MODE = "default"
HOOK_TIMEOUT_SECONDS = 30
# Subcommands that run a helper program through a transport, a filter, or
# a help viewer.
GIT_HELPER_COMMANDS = (
    "git ls-remote ext::sh",
    "git cat-file --textconv HEAD:README.md",
    "git help log",
)
# Reads that a configured external diff program turns into a program run.
CONFIGURED_HELPER_READS = ("git diff", "git ls-files")
GATE_HOOKS = (("bash", BASH_HOOK, "Bash"), ("powershell", POWERSHELL_HOOK, "PowerShell"),
              ("cmd", CMD_HOOK, "Cmd"))
GIT_WRITE_COMMANDS = (
    "git commit -m change",
    "git branch -m fix/rename-branch",
    "git push origin feat/x",
    "git switch -c fix/new-branch",
    "git checkout -b fix/new-branch",
    "git add README.md",
    "git -C /repo commit -m change",
)
GIT_READ_COMMANDS = (
    "git status",
    "git log --oneline -5",
    "git diff",
    "git show HEAD",
)
# Inspection subcommands with options that write files or run programs.
GIT_UNSAFE_OPTION_COMMANDS = (
    "git diff --output=changes.txt",
    "git diff --output changes.txt",
    "git grep -O cat pattern",
    "git grep -Ocat pattern",
    "git grep --open-files-in-pager=cat pattern",
    "git log --ext-diff",
    "git diff --textconv",
    "git -c core.pager=cat status",
    "git --config-env=core.pager=PAGER status",
    "git --paginate log",
    "git -p log",
    "git --exec-path=/tmp status",
    "git format-patch -o out HEAD~1",
)
GIT_SAFE_OPTION_COMMANDS = (
    "git log --oneline -5",
    "git diff --stat",
    "git show --oneline HEAD",
)
GATES = (("bash", bash), ("powershell", powershell), ("cmd", cmd))


class PlanModeGitWriteTest(unittest.TestCase):
    """Plan mode denies Git writes in every shell gate."""

    def test_git_writes_deny(self):
        for command in GIT_WRITE_COMMANDS:
            for gate_name, gate in GATES:
                with self.subTest(command=command, gate=gate_name):
                    self.assertEqual(gate(command, PLAN_MODE), "deny")

    def test_git_reads_pass(self):
        for command in GIT_READ_COMMANDS:
            for gate_name, gate in GATES:
                with self.subTest(command=command, gate=gate_name):
                    self.assertEqual(gate(command, PLAN_MODE), "")

    def test_unsafe_options_deny(self):
        for command in GIT_UNSAFE_OPTION_COMMANDS:
            for gate_name, gate in GATES:
                with self.subTest(command=command, gate=gate_name):
                    self.assertEqual(gate(command, PLAN_MODE), "deny")

    def test_safe_options_pass(self):
        for command in GIT_SAFE_OPTION_COMMANDS:
            for gate_name, gate in GATES:
                with self.subTest(command=command, gate=gate_name):
                    self.assertEqual(gate(command, PLAN_MODE), "")

    def test_default_mode_allows_writes(self):
        for gate_name, gate in GATES:
            with self.subTest(gate=gate_name):
                self.assertEqual(gate("git commit -m change"), "")


class PlanModeHelperTest(unittest.TestCase):
    """Plan mode denies Git reads that run a helper program."""

    def test_helper_subcommands_deny(self):
        for command in GIT_HELPER_COMMANDS:
            for gate_name, gate in GATES:
                with self.subTest(command=command, gate=gate_name):
                    self.assertEqual(gate(command, PLAN_MODE), "deny")

    def test_configured_external_diff_denies(self):
        with tempfile.TemporaryDirectory() as root:
            git_dir = Path(root) / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text(
                "[core]\n\tbare = false\n[diff]\n\texternal = /tmp/helper\n",
                encoding="utf-8")
            for command in CONFIGURED_HELPER_READS:
                for gate_name, hook, tool in GATE_HOOKS:
                    with self.subTest(command=command, gate=gate_name):
                        self.assertEqual(
                            _decision(hook, tool, command, PLAN_MODE, root), "deny")
            for gate_name, hook, tool in GATE_HOOKS:
                with self.subTest(command="git diff", gate=gate_name, mode=DEFAULT_MODE):
                    self.assertEqual(
                        _decision(hook, tool, "git diff", DEFAULT_MODE, root), "ask")

    def test_bash_pager_assignment_denies(self):
        self.assertEqual(bash("GIT_PAGER=less git log", PLAN_MODE), "deny")


class EnvironmentAssignmentTest(unittest.TestCase):
    """An in-command Git variable assignment asks, and plan mode denies it."""

    ASSIGNMENTS = (
        ("bash", bash, "export GIT_PAGER=less; git log"),
        ("powershell", powershell, "$env:GIT_PAGER='less'; git log"),
        ("cmd", cmd, "set GIT_PAGER=less && git log"),
    )
    CMD_ASKS = ('set "GIT_PAGER=less"', "set GIT_EXTERNAL_DIFF=x", "set /a GIT_PAGER=1")

    def test_assignment_denies_in_plan_mode(self):
        for gate_name, gate, command in self.ASSIGNMENTS:
            with self.subTest(gate=gate_name):
                self.assertEqual(gate(command, PLAN_MODE), "deny")

    def test_assignment_asks_in_default_mode(self):
        for gate_name, gate, command in self.ASSIGNMENTS:
            with self.subTest(gate=gate_name):
                self.assertEqual(gate(command, DEFAULT_MODE), "ask")

    def test_cmd_set_forms_ask(self):
        for command in self.CMD_ASKS:
            with self.subTest(command=command):
                self.assertEqual(cmd(command, DEFAULT_MODE), "ask")

    def test_cmd_set_unrelated_passes(self):
        for command in ("set FOO=1", "set", "set /a"):
            with self.subTest(command=command):
                self.assertEqual(cmd(command, DEFAULT_MODE), "")


def run_with_environment(hook: Path, tool: str, command: str, mode: str,
                         cwd: str, environment: dict) -> str:
    """Run one gate in a fresh process with `environment` and return its decision."""
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
               "permission_mode": mode, "cwd": cwd,
               "tool_input": {"command": command}}
    result = subprocess.run(
        [sys.executable, str(hook)], input=json.dumps(payload),
        capture_output=True, text=True, env=environment, check=False,
        timeout=HOOK_TIMEOUT_SECONDS)
    try:
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError):
        return ""


class InheritedEnvironmentTest(unittest.TestCase):
    """Each gate reads Git helper variables inherited from its own process."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        git_dir = Path(self.root) / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
        self.clean = {key: value for key, value in os.environ.items()
                      if not key.startswith("GIT_") and key not in ("PAGER",)}
        self.clean["GIT_CONFIG_NOSYSTEM"] = "1"
        self.clean["GIT_CONFIG_GLOBAL"] = os.devnull

    def decisions(self, mode: str, extra: dict) -> dict:
        """Return each gate's decision for `git diff` with `extra` inherited."""
        environment = dict(self.clean, **extra)
        return {gate_name: run_with_environment(hook, tool, "git diff", mode,
                                                self.root, environment)
                for gate_name, hook, tool in GATE_HOOKS}

    def test_inherited_external_diff_denies(self):
        for gate_name, decision in self.decisions(
                PLAN_MODE, {"GIT_EXTERNAL_DIFF": "/tmp/helper"}).items():
            with self.subTest(gate=gate_name):
                self.assertEqual(decision, "deny")

    def test_inherited_pager_denies(self):
        for gate_name, decision in self.decisions(
                PLAN_MODE, {"GIT_PAGER": "less"}).items():
            with self.subTest(gate=gate_name):
                self.assertEqual(decision, "deny")

    def test_clean_environment_passes(self):
        for gate_name, decision in self.decisions(PLAN_MODE, {}).items():
            with self.subTest(gate=gate_name):
                self.assertEqual(decision, "")


if __name__ == "__main__":
    unittest.main()
