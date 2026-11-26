#!/usr/bin/env python3
"""The read-only `run api` allow admits only plain REST paths and never GraphQL."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_command_execution_gate import _load_gate_module
from tests.test_enforce_branch_name import BLOCKING_EXIT_CODE, HOOK_PATH, REPO_ROOT

GATE = _load_gate_module()
PROJECT_DIR = str(REPO_ROOT)
WRAPPER = "python scripts/trusted_gh.py run "
TIMEOUT_SECONDS = 15
FEATURE_BRANCH = "feat/example-change"
SHELL_TOOL_NAMES = ("Bash", "PowerShell", "Cmd")

# Each path below names the GraphQL endpoint or an ambiguous spelling of a
# REST path. None may pass without a prompt.
REJECTED_PATHS = (
    "graphql?query=x",
    "graphql/",
    "GraphQL/",
    "/graphql",
    "//graphql",
    "./graphql",
    "../graphql",
    "%67raphql",
    "graphql%3Fquery=x",
    "graphql#x",
    "repos/OWNER/REPO/issues?state=open%20x",
    "repos/OWNER/REPO/issues?a=1?b=2",
    "repos//OWNER",
    "repos/OWNER/REPO/",
)
# An unquoted shell backslash escapes the next character, so a command line
# passes `reposOWNER` here. The helper still rejects the literal backslash.
HELPER_ONLY_REJECTED_PATHS = ("repos\\OWNER",)

ACCEPTED_PATHS = (
    "user",
    "/user",
    "repos/OWNER/REPO/issues?state=open",
    "repos/OWNER/REPO/issues?per_page=100",
    "repos/OWNER/REPO/contents/docs/a.md",
)

# These reach the option scan and must leave the call at its earlier verdict.
INCOMPLETE_OR_NON_GET_REQUESTS = (
    "api repos/OWNER/REPO --jq",
    "api repos/OWNER/REPO -X",
    "api -X HEAD repos/OWNER/REPO",
    "api -X get repos/OWNER/REPO",
)


def api_decision(arguments: str) -> str:
    """Return the command gate decision for one wrapper `run` call."""
    decision, _reason = GATE.workflow_decision(WRAPPER + arguments, PROJECT_DIR)
    return decision


class ApiReadPathShapeTest(unittest.TestCase):
    """Only plain segments and a plain query pass the read allow."""

    def test_graphql_paths_not_allowed(self):
        for path in REJECTED_PATHS:
            with self.subTest(path=path):
                self.assertNotEqual(api_decision(f"api {path}"), "allow")

    def test_plain_rest_paths_stay_allowed(self):
        for path in ACCEPTED_PATHS:
            with self.subTest(path=path):
                self.assertEqual(api_decision(f"api {path}"), "allow")

    def test_incomplete_non_get_requests(self):
        for arguments in INCOMPLETE_OR_NON_GET_REQUESTS:
            with self.subTest(arguments=arguments):
                self.assertNotEqual(api_decision(arguments), "allow")

    def test_helper_rejects_graphql_paths(self):
        for path in REJECTED_PATHS + HELPER_ONLY_REJECTED_PATHS:
            with self.subTest(path=path):
                self.assertFalse(GATE._api_read_path_safe(path))

    def test_helper_accepts_plain_rest_paths(self):
        for path in ACCEPTED_PATHS:
            with self.subTest(path=path):
                self.assertTrue(GATE._api_read_path_safe(path))

    def test_option_scan_rejects_invalid(self):
        cases = (
            ("repos/OWNER/REPO", "--jq"),
            ("repos/OWNER/REPO", "-X"),
            ("-X", "HEAD", "repos/OWNER/REPO"),
            ("--method", "get", "repos/OWNER/REPO"),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(GATE._api_read_path(arguments), "")


class ApiReadPathHookTest(unittest.TestCase):
    """The registered branch hook keeps GraphQL path variants off the read allow."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        git_dir = self.root / ".git"
        (git_dir / "objects").mkdir(parents=True)
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "HEAD").write_text(f"ref: refs/heads/{FEATURE_BRANCH}\n", encoding="utf-8")
        (git_dir / "config").write_text(
            "[core]\nrepositoryformatversion = 0\nbare = false\n"
            '[remote "origin"]\nurl = https://github.com/example-owner/example-repo.git\n',
            encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "trusted_gh.py").write_bytes(
            (REPO_ROOT / "scripts" / "trusted_gh.py").read_bytes())
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("GIT_")}
        self.environment.pop("GITHUB_HEAD_REF", None)
        self.environment["CLAUDE_PROJECT_DIR"] = str(self.root)

    def decision(self, tool_name: str, command: str) -> str:
        """Return allow, ask, or deny for one shell tool payload."""
        payload = {"hook_event_name": "PreToolUse", "permission_mode": "default",
                   "tool_name": tool_name, "tool_input": {"command": command},
                   "cwd": str(self.root)}
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH), "--client", "claude"],
            input=json.dumps(payload), env=self.environment, capture_output=True,
            text=True, timeout=TIMEOUT_SECONDS, check=False)
        if result.returncode == BLOCKING_EXIT_CODE:
            return "deny"
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return "allow"
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]

    def test_graphql_variants_prompt(self):
        for path in ("graphql?query=x", "graphql/"):
            for tool_name in SHELL_TOOL_NAMES:
                with self.subTest(path=path, tool=tool_name):
                    self.assertNotEqual(
                        self.decision(tool_name, WRAPPER + f"api {path}"), "allow")


if __name__ == "__main__":
    unittest.main()
