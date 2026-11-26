#!/usr/bin/env python3
"""The GitHub MCP write hook denies, asks, or passes each tool by its action."""
import contextlib
import importlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPOSITORY_ROOT / "hooks" / "deny_mcp_github_writes.py"
HOOK_TIMEOUT_SECONDS = 15
POLICY_DENIAL_EXIT_CODE = 2
DENIED_ACTIONS = ("add_issue_comment", "add_reply_to_pull_request_comment",
                  "pull_request_review_write", "add_comment_to_pending_review",
                  "update_issue_comment", "merge_pull_request", "enable_pr_auto_merge",
                  "create_pull_request_with_copilot")
ASKED_ACTIONS = ("push_files", "create_or_update_file", "delete_file", "fork_repository",
                 "create_repository", "create_branch", "issue_write", "sub_issue_write",
                 "resolve_review_thread", "unresolve_review_thread", "request_copilot_review",
                 "assign_copilot_to_issue", "actions_run_trigger", "update_pull_request_branch",
                 "disable_pr_auto_merge")


def run_hook(raw: str):
    """Run the hook on raw stdin and return (exit code, permission decision, reason)."""
    result = subprocess.run([sys.executable, str(HOOK_PATH)], input=raw, text=True,
                            capture_output=True, timeout=HOOK_TIMEOUT_SECONDS, check=False,
                            cwd=REPOSITORY_ROOT)
    parsed = json.loads(result.stdout) if result.stdout.strip() else {}
    output = parsed.get("hookSpecificOutput", {})
    return result.returncode, output.get("permissionDecision", ""), \
        output.get("permissionDecisionReason", "")


def payload(action: str, tool_input=None, server: str = "github",
            mode: str = "default") -> str:
    """Return a PreToolUse payload for one MCP tool call."""
    return json.dumps({"hook_event_name": "PreToolUse", "permission_mode": mode,
                       "cwd": str(REPOSITORY_ROOT), "tool_name": f"mcp__{server}__{action}",
                       "tool_input": {} if tool_input is None else tool_input})


class GithubWriteHookTest(unittest.TestCase):
    """Comment, merge, label, and non-draft writes deny. Other writes ask. Reads pass."""

    def test_denied_actions(self) -> None:
        for action in DENIED_ACTIONS:
            with self.subTest(action=action):
                code, verdict, _ = run_hook(payload(action, {"body": "x"}))
                self.assertEqual((code, verdict), (POLICY_DENIAL_EXIT_CODE, "deny"))

    def test_comment_denial_names_wrapper(self) -> None:
        _code, _verdict, reason = run_hook(payload("add_issue_comment", {"body": "x"}))
        self.assertIn("scripts/trusted_gh.py comment", reason)

    def test_asked_actions(self) -> None:
        for action in ASKED_ACTIONS:
            with self.subTest(action=action):
                code, verdict, _ = run_hook(payload(action, {"owner": "o"}))
                self.assertEqual((code, verdict), (0, "ask"))

    def test_unattended_mode_denies_ask(self) -> None:
        code, verdict, _ = run_hook(payload("push_files", {"owner": "o"}, mode="dontAsk"))
        self.assertEqual((code, verdict), (POLICY_DENIAL_EXIT_CODE, "deny"))

    def test_pull_request_draft_rules(self) -> None:
        cases = (("create_pull_request", {"title": "t"}, "deny"),
                 ("create_pull_request", {"title": "t", "draft": False}, "deny"),
                 ("create_pull_request", {"title": "t", "draft": True}, "ask"),
                 ("update_pull_request", {"draft": False}, "deny"),
                 ("update_pull_request", {"title": "t"}, "ask"),
                 ("update_pull_request", {"draft": True}, "ask"))
        for action, tool_input, expected in cases:
            with self.subTest(action=action, tool_input=tool_input):
                _code, verdict, _ = run_hook(payload(action, tool_input))
                self.assertEqual(verdict, expected)

    def test_labels_deny_on_any_tool(self) -> None:
        for action in ("issue_write", "create_pull_request", "get_me"):
            with self.subTest(action=action):
                _code, verdict, _ = run_hook(payload(action, {"draft": True,
                                                              "labels": ["bug"]}))
                self.assertEqual(verdict, "deny")

    def test_empty_labels_deny(self) -> None:
        for action in ("issue_write", "update_pull_request"):
            for value in ([], None):
                with self.subTest(action=action, value=value):
                    _code, verdict, _ = run_hook(payload(action, {"labels": value}))
                    self.assertEqual(verdict, "deny")

    def test_read_tools_pass(self) -> None:
        for action in ("get_me", "pull_request_read", "list_pull_requests", "get_file_contents"):
            with self.subTest(action=action):
                code, verdict, _ = run_hook(payload(action, {"owner": "o"}))
                self.assertEqual((code, verdict), (0, ""))

    def test_any_server_prefix_matches(self) -> None:
        _code, verdict, _ = run_hook(payload("merge_pull_request", {},
                                             server="plugin_engineering_github"))
        self.assertEqual(verdict, "deny")

    def test_non_mcp_tool_passes(self) -> None:
        raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {"command": "ls"}})
        self.assertEqual(run_hook(raw)[:2], (0, ""))

    def test_non_string_tool_name_denies(self) -> None:
        raw = json.dumps({"tool_name": 7, "tool_input": {}})
        self.assertEqual(run_hook(raw)[:2], (POLICY_DENIAL_EXIT_CODE, "deny"))

    def test_malformed_input_denies(self) -> None:
        for raw in ("not json", json.dumps({"tool_name": "mcp__github__push_files",
                                            "tool_input": "x"})):
            with self.subTest(raw=raw[:30]):
                code, verdict, _ = run_hook(raw)
                self.assertEqual((code, verdict), (POLICY_DENIAL_EXIT_CODE, "deny"))



class UnexpectedErrorTest(unittest.TestCase):
    """An unexpected error inside the decision still denies the call."""

    def test_unexpected_error_denies(self) -> None:
        sys.path.insert(0, str(REPOSITORY_ROOT / "hooks"))
        module = importlib.import_module("deny_mcp_github_writes")
        stdin = io.StringIO(json.dumps({"tool_name": "mcp__github__push_files", "tool_input": {}}))
        output = io.StringIO()
        with patch.object(module, "write_decision", side_effect=RuntimeError("boom")), \
                patch.object(module.sys, "stdin", stdin), contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(io.StringIO()):
            code = module.main()
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertIn("unexpected error", output.getvalue())


if __name__ == "__main__":
    unittest.main()
