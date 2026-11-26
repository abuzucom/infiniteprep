#!/usr/bin/env python3
"""Pull requests target the primary branch, where every CI workflow runs."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.test_trusted_gh_rest_write_forms as forms
from tests.test_command_execution_gate import _load_gate_module

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPOSITORY_ROOT / "hooks" / "enforce_pr_base.py"
GATE = _load_gate_module()
CORE = GATE.core
POLICY_DENIAL_EXIT_CODE = 2
HOOK_TIMEOUT_SECONDS = 15
PR_EDIT = ["pr", "edit", "7"]
OTHER_BASE = "docs/stacked-branch"
BASE_SPELLINGS = (["--base", OTHER_BASE], [f"--base={OTHER_BASE}"], ["-B", OTHER_BASE],
                  [f"-B{OTHER_BASE}"])


class WrapperBaseTest(forms.RestWriteHarness):
    """trusted_gh.py refuses any pull request base other than the primary branch."""

    def assert_base_refused(self, argv: list) -> None:
        code, _stdout, stderr = self.run_wrapper(argv)
        self.assertEqual(code, forms.USAGE_EXIT_CODE, stderr)
        self.assertIn("primary branch", stderr)
        self.assertEqual(self.calls, [])

    def test_create_other_base_refused(self):
        for spelling in BASE_SPELLINGS:
            with self.subTest(spelling=spelling):
                self.calls.clear()
                self.assert_base_refused([*forms.PR_CREATE, *spelling])

    def test_edit_other_base_refused(self):
        for spelling in BASE_SPELLINGS:
            with self.subTest(spelling=spelling):
                self.calls.clear()
                self.assert_base_refused([*PR_EDIT, *spelling])

    @unittest.skipUnless(forms.SAFE_DRAFT_OPEN, forms.NO_SAFE_DRAFT_OPEN)
    def test_primary_base_passes(self):
        code, _stdout, stderr = self.run_wrapper([*forms.PR_CREATE, "--base", "main"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.sent_payloads()[0]["base"], "main")

    def test_edit_primary_base_passes(self):
        code, _stdout, stderr = self.run_wrapper([*PR_EDIT, "--base", "main"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.calls), 1)

    def test_override_names_only_base(self):
        self.write_primary_branch("trunk\n")
        self.assert_base_refused([*PR_EDIT, "--base", "main"])
        self.calls.clear()
        code, _stdout, stderr = self.run_wrapper([*PR_EDIT, "--base", "trunk"])
        self.assertEqual(code, 0, stderr)

    def test_unrelated_commands_unchanged(self):
        code, _stdout, stderr = self.run_wrapper(["pr", "view", "7", "--json", "baseRefName"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.calls), 1)


class WrapperDefaultBaseTest(forms.RestWriteHarness):
    """A pull request create without a base gets the primary branch before gh runs."""

    def context(self, argv: list) -> list:
        with patch.object(forms.trusted_gh, "repository_target", return_value=forms.REPOSITORY):
            return forms.trusted_gh.with_repository_context(self.root, argv)

    def test_missing_base_gets_primary(self):
        arguments = self.context(["pr", "create", "--draft", "--title", "T"])
        self.assertEqual(arguments[arguments.index("--base") + 1], "main")

    def test_override_names_default_base(self):
        self.write_primary_branch("trunk\n")
        arguments = self.context(["pr", "create", "--draft", "--title", "T"])
        self.assertEqual(arguments[arguments.index("--base") + 1], "trunk")

    def test_explicit_base_kept(self):
        for spelling in (["--base", "main"], ["--base=main"], ["-B", "main"], ["-Bmain"]):
            with self.subTest(spelling=spelling):
                arguments = self.context(["pr", "create", "--draft", *spelling])
                self.assertEqual(arguments.count("--base"), spelling.count("--base"))

    def test_other_commands_unchanged(self):
        self.assertNotIn("--base", self.context(["pr", "edit", "7", "--title", "T"]))

    def test_unusable_override_refuses(self):
        self.write_primary_branch("a b\nc\n")
        code, _stdout, stderr = self.run_wrapper(["pr", "create", "--draft", "--title", "T"])
        self.assertEqual(code, forms.USAGE_EXIT_CODE, stderr)
        self.assertEqual(self.calls, [])


class GateBaseTest(unittest.TestCase):
    """The shell gates deny a pull request command with a non-primary base."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".git").mkdir()
        CORE.reset_invocation_caches()

    def verdict(self, args: list) -> str:
        return CORE.forge_verdict("gh", args, str(self.root))[0]

    def test_other_base_denies(self):
        for command in (["pr", "create", "--draft", "--title", "T"], PR_EDIT):
            for spelling in BASE_SPELLINGS:
                with self.subTest(command=command, spelling=spelling):
                    self.assertEqual(self.verdict([*command, *spelling]), "deny")

    def test_primary_base_not_denied(self):
        self.assertNotEqual(self.verdict(["pr", "create", "--draft", "--base", "main"]), "deny")
        self.assertNotEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")

    def test_direct_gh_without_base_denies(self):
        # The shell gates route every direct gh call to the wrapper, which adds the primary base.
        decision, reason = CORE.github_routing_verdict(
            "gh", ["pr", "create", "--draft", "--title", "T"], str(self.root))
        self.assertEqual(decision, "deny", reason)
        self.assertIn("trusted_gh.py", reason)

    def test_wrapper_form_denies(self):
        arguments = ["scripts/trusted_gh.py", "run", *PR_EDIT, "--base", OTHER_BASE]
        decision, reason = CORE.forge_verdict("python", arguments, str(self.root))
        self.assertEqual(decision, "deny", reason)
        command = " ".join(["python", *arguments])
        self.assertNotIn(GATE.workflow_decision(command, str(self.root))[0], ("allow", "ask"))

    def test_override_and_invalid_file(self):
        hooks = self.root / "hooks"
        hooks.mkdir()
        (hooks / "primary-branch.txt").write_text("trunk\n", encoding="utf-8")
        self.assertEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")
        self.assertNotEqual(self.verdict([*PR_EDIT, "--base", "trunk"]), "deny")
        (hooks / "primary-branch.txt").write_text("a b\nc\n", encoding="utf-8")
        self.assertEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")

    def test_irregular_override_denies(self):
        hooks = self.root / "hooks"
        hooks.mkdir()
        (hooks / "primary-branch.txt").mkdir()
        self.assertEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_linked_override_denies(self):
        hooks = self.root / "hooks"
        hooks.mkdir()
        target = self.root / "elsewhere.txt"
        target.write_text("main\n", encoding="utf-8")
        (hooks / "primary-branch.txt").symlink_to(target)
        self.assertEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")

    def test_unreadable_override_denies(self):
        # The patched lstat raises an error other than an absent file on every platform.
        with patch.object(CORE.os, "lstat", side_effect=PermissionError("denied")):
            self.assertEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")

    def test_failed_open_denies(self):
        hooks = self.root / "hooks"
        hooks.mkdir()
        (hooks / "primary-branch.txt").write_text("main\n", encoding="utf-8")
        # The patched open raises the error a mode 000 file gives an ordinary user.
        with patch.object(CORE.os, "open", side_effect=PermissionError("denied")):
            self.assertEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_link_denies_without_nofollow(self):
        hooks = self.root / "hooks"
        hooks.mkdir()
        target = self.root / "elsewhere.txt"
        target.write_text("main\n", encoding="utf-8")
        (hooks / "primary-branch.txt").symlink_to(target)
        # Windows lacks O_NOFOLLOW. Plain flags reproduce its open behavior here.
        with patch.object(CORE, "PRIMARY_READ_FLAGS", os.O_RDONLY):
            self.assertEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")


def _run_hook(payload, project_dir: Path) -> subprocess.CompletedProcess:
    """Run the MCP base hook with one payload."""
    environment = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK_PATH)], input=text, capture_output=True,
                          text=True, env=environment, check=False, timeout=HOOK_TIMEOUT_SECONDS)


class McpBaseHookTest(unittest.TestCase):
    """GitHub MCP pull request tools may target only the primary branch."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def call(self, tool: str, tool_input: dict) -> subprocess.CompletedProcess:
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
        return _run_hook(payload, self.root)

    def test_other_base_denies(self):
        for tool in ("mcp__github__create_pull_request", "mcp__other__update_pull_request"):
            with self.subTest(tool=tool):
                result = self.call(tool, {"base": OTHER_BASE, "title": "T"})
                self.assertEqual(result.returncode, POLICY_DENIAL_EXIT_CODE)
                self.assertIn("primary branch", result.stdout)

    def test_primary_or_absent_base_passes(self):
        cases = (("mcp__github__create_pull_request", {"base": "main"}),
                 ("mcp__github__update_pull_request", {"title": "T"}),
                 ("mcp__github__list_pull_requests", {"base": OTHER_BASE}),
                 ("create_pull_request", {"base": OTHER_BASE}))
        for tool, tool_input in cases:
            with self.subTest(tool=tool):
                self.assertEqual(self.call(tool, tool_input).returncode, 0)

    def test_malformed_input_denies(self):
        self.assertEqual(_run_hook("not json", self.root).returncode, POLICY_DENIAL_EXIT_CODE)
        result = self.call("mcp__github__create_pull_request", {"base": 7})
        self.assertEqual(result.returncode, POLICY_DENIAL_EXIT_CODE)
        wrong_types = {"hook_event_name": "PreToolUse", "tool_name": 5, "tool_input": []}
        self.assertEqual(_run_hook(wrong_types, self.root).returncode, POLICY_DENIAL_EXIT_CODE)

    def test_unexpected_error_denies(self):
        environment = {key: value for key, value in os.environ.items()
                       if key != "CLAUDE_PROJECT_DIR"}
        payload = {"hook_event_name": "PreToolUse", "cwd": 5,
                   "tool_name": "mcp__github__create_pull_request",
                   "tool_input": {"base": OTHER_BASE}}
        result = subprocess.run([sys.executable, str(HOOK_PATH)], input=json.dumps(payload),
                                capture_output=True, text=True, env=environment, check=False,
                                timeout=HOOK_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, POLICY_DENIAL_EXIT_CODE)
        self.assertIn("unexpected error", result.stdout)


if __name__ == "__main__":
    unittest.main()
