#!/usr/bin/env python3
"""The command gate allows read-only wrapper `run api` GET requests without a prompt."""
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
SHA_HEX_LENGTH = 40
FEATURE_BRANCH = "feat/example-change"
SHELL_TOOL_NAMES = ("Bash", "PowerShell", "Cmd")
# Every hook fixture copies the same wrapper. One read serves every test.
TRUSTED_GH_BYTES = (REPO_ROOT / "scripts" / "trusted_gh.py").read_bytes()

ALLOWED_READS = (
    "api user",
    "api repos/OWNER/REPO/issues/5",
    "api repos/OWNER/REPO/issues?state=open",
    "api -X GET repos/OWNER/REPO/issues/5",
    "api --method GET repos/OWNER/REPO/pulls",
    "api repos/OWNER/REPO/issues/5 --jq .title",
    "api repos/OWNER/REPO/issues/5 -q .body",
    "api --paginate repos/OWNER/REPO/issues",
    "api --paginate --slurp repos/OWNER/REPO/issues",
    "api -i repos/OWNER/REPO",
    "api --include repos/OWNER/REPO",
    "api --silent repos/OWNER/REPO",
    "api -X GET --method GET repos/OWNER/REPO",
)

# Each request below must keep a prompt or a denial. None may become allow.
NOT_ALLOWED_REQUESTS = (
    "api -X POST repos/OWNER/REPO/issues",
    "api -X PATCH repos/OWNER/REPO/issues/5",
    "api -X PUT repos/OWNER/REPO/issues/5/lock",
    "api -X DELETE repos/OWNER/REPO/issues/5/lock",
    "api --method POST repos/OWNER/REPO/issues",
    "api -X GET -X POST repos/OWNER/REPO/issues",
    "api repos/OWNER/REPO/issues -f title=value",
    "api repos/OWNER/REPO/issues -F title=value",
    "api repos/OWNER/REPO/issues --field title=value",
    "api repos/OWNER/REPO/issues --raw-field title=value",
    "api repos/OWNER/REPO/issues --input body.json",
    "api -X GET repos/OWNER/REPO/issues -f state=open",
    "api repos/OWNER/REPO -H Accept:application/json",
    "api repos/OWNER/REPO --header Accept:application/json",
    "api --hostname github.example repos/OWNER/REPO",
    "api graphql",
    "api GraphQL",
    "api GRAPHQL",
    "api https://api.github.com/repos/OWNER/REPO",
    "api http://api.github.com/repos/OWNER/REPO",
    "api repos/OWNER/REPO --cache 1h",
    "api repos/OWNER/REPO --verbose",
    "api repos/OWNER/REPO repos/OWNER/OTHER",
    "api",
    "api --jq .id",
    "pr list",
    "issue view 5",
    "repo view OWNER/REPO",
)


def wrapper_decision(arguments: str, project_dir: str = PROJECT_DIR) -> str:
    """Return the command gate decision for one wrapper `run` call."""
    decision, _reason = GATE.workflow_decision(WRAPPER + arguments, project_dir)
    return decision


class ApiReadVerdictTest(unittest.TestCase):
    """Read-only GET requests pass and every other wrapper call keeps its verdict."""

    def test_get_requests_allowed(self):
        for arguments in ALLOWED_READS:
            with self.subTest(arguments=arguments):
                self.assertEqual(wrapper_decision(arguments), "allow")

    def test_get_requests_read_only(self):
        for arguments in ALLOWED_READS:
            with self.subTest(arguments=arguments):
                self.assertTrue(GATE.read_only_workflow(WRAPPER + arguments, PROJECT_DIR))

    def test_other_requests_are_never_allowed(self):
        for arguments in NOT_ALLOWED_REQUESTS:
            with self.subTest(arguments=arguments):
                self.assertIn(wrapper_decision(arguments), ("ask", "deny", ""))

    # The workflow gate leaves a forge-denied wrapper call unrecognized. The
    # branch hook then denies it through command_execution_violation.
    # ApiReadHookTest checks that path end to end.
    def test_mutating_methods_stay_unrecognized(self):
        for method in ("POST", "PATCH", "PUT", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(
                    wrapper_decision(f"api -X {method} repos/OWNER/REPO/issues/5"), "")

    def test_field_options_stay_unrecognized(self):
        for option in ("-f", "-F", "--field", "--raw-field"):
            with self.subTest(option=option):
                self.assertEqual(
                    wrapper_decision(f"api repos/OWNER/REPO/issues {option} title=value"), "")

    def test_non_api_calls_ask(self):
        for arguments in ("pr list", "issue view 5", "repo view OWNER/REPO"):
            with self.subTest(arguments=arguments):
                self.assertEqual(wrapper_decision(arguments), "ask")

    def test_chained_get_asks(self):
        command = WRAPPER + "api user && " + WRAPPER + "pr list"
        decision, _reason = GATE.workflow_decision(command, PROJECT_DIR)
        self.assertEqual(decision, "ask")

    def test_other_script_get_not_allowed(self):
        decision, _reason = GATE.workflow_decision(
            "python scripts/trusted_git.py run api user", PROJECT_DIR)
        self.assertNotEqual(decision, "allow")


class ApiRepositoryFixture:
    """Build one repository with an origin remote and the wrapper script."""

    def __init__(self, test_case: unittest.TestCase):
        temporary = tempfile.TemporaryDirectory()
        test_case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        git_dir = self.root / ".git"
        (git_dir / "objects").mkdir(parents=True)
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "config").write_text(
            "[core]\nrepositoryformatversion = 0\nbare = false\n"
            '[remote "origin"]\nurl = https://github.com/example-owner/example-repo.git\n',
            encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "trusted_gh.py").write_bytes(TRUSTED_GH_BYTES)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("GIT_")}
        self.environment.pop("GITHUB_HEAD_REF", None)
        self.environment["CLAUDE_PROJECT_DIR"] = str(self.root)

    def set_head(self, branch: str) -> None:
        """Point HEAD at one branch, or detach it for `HEAD`."""
        head = "0" * SHA_HEX_LENGTH if branch == "HEAD" else f"ref: refs/heads/{branch}"
        (self.root / ".git" / "HEAD").write_text(head + "\n", encoding="utf-8")


class ApiHookRunner:
    """Send shell tool payloads to the installed hook for one repository."""

    def __init__(self, root: Path, environment: dict):
        self.root = root
        self.environment = environment

    def run_hook(self, tool_name: str, command: str) -> subprocess.CompletedProcess:
        """Send one shell tool payload to the hook without executing the command."""
        payload = {"hook_event_name": "PreToolUse", "permission_mode": "default",
                   "tool_name": tool_name, "tool_input": {"command": command},
                   "cwd": str(self.root)}
        return subprocess.run(
            [sys.executable, str(HOOK_PATH), "--client", "claude"],
            input=json.dumps(payload), env=self.environment, capture_output=True,
            text=True, timeout=TIMEOUT_SECONDS, check=False)


def read_hook_decision(test_case: unittest.TestCase,
                       result: subprocess.CompletedProcess) -> str:
    """Return allow, ask, or deny from one hook result.

    A launch failure other than a blocking exit fails the calling test.
    """
    if result.returncode == BLOCKING_EXIT_CODE:
        return "deny"
    test_case.assertEqual(result.returncode, 0, result.stderr)
    if not result.stdout.strip():
        return "allow"
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


class ApiHookFixture:
    """Compose the repository fixture, hook runner, and decision reader.

    Each test class builds one in setUp and inherits only unittest.TestCase.
    Each class stays small because the hook coverage runner gives every test
    class one shard with a fixed time limit, and every case launches the hook.
    """

    def __init__(self, test_case: unittest.TestCase):
        self.test_case = test_case
        self.repository = ApiRepositoryFixture(test_case)
        self.runner = ApiHookRunner(self.repository.root, self.repository.environment)

    def set_head(self, branch: str) -> None:
        """Point HEAD at one branch, or detach it for `HEAD`."""
        self.repository.set_head(branch)

    def run_hook(self, tool_name: str, command: str) -> subprocess.CompletedProcess:
        """Send one shell tool payload to the hook without executing the command."""
        return self.runner.run_hook(tool_name, command)

    def decision(self, result: subprocess.CompletedProcess) -> str:
        """Return allow, ask, or deny from one hook result."""
        return read_hook_decision(self.test_case, result)


class ApiReadHookTest(unittest.TestCase):
    """The registered branch hook applies the allow to every shell tool and branch state."""

    def setUp(self):
        self.hook = ApiHookFixture(self)

    def test_get_passes_without_prompt(self):
        for branch in (FEATURE_BRANCH, "main", "HEAD"):
            self.hook.set_head(branch)
            for tool_name in SHELL_TOOL_NAMES:
                with self.subTest(branch=branch, tool=tool_name):
                    result = self.hook.run_hook(
                        tool_name, WRAPPER + "api repos/OWNER/REPO/issues/5")
                    self.assertEqual(self.hook.decision(result), "allow")


class ApiNonGetHookTest(unittest.TestCase):
    """Wrapper calls other than a plain GET keep their verdict."""

    def setUp(self):
        self.hook = ApiHookFixture(self)

    def test_non_get_verdict_kept(self):
        self.hook.set_head(FEATURE_BRANCH)
        cases = (
            ("pr list", "ask"),
            ("api -X POST repos/OWNER/REPO/issues", "deny"),
            ("api -X PATCH repos/OWNER/REPO/issues/5", "deny"),
            ("api -X PUT repos/OWNER/REPO/issues/5/lock", "deny"),
            ("api -X DELETE repos/OWNER/REPO/issues/5/lock", "deny"),
            ("api repos/OWNER/REPO/issues -f title=value", "deny"),
            ("api repos/OWNER/REPO/issues -F title=value", "deny"),
            ("api repos/OWNER/REPO/issues --field title=value", "deny"),
            ("api repos/OWNER/REPO/issues --raw-field title=value", "deny"),
        )
        for arguments, expected in cases:
            for tool_name in SHELL_TOOL_NAMES:
                with self.subTest(arguments=arguments, tool=tool_name):
                    result = self.hook.run_hook(tool_name, WRAPPER + arguments)
                    self.assertEqual(self.hook.decision(result), expected)


class ApiHeaderHookTest(unittest.TestCase):
    """Header, hostname, GraphQL, and full-URL requests never pass."""

    def setUp(self):
        self.hook = ApiHookFixture(self)

    def test_header_graphql_prompt(self):
        self.hook.set_head(FEATURE_BRANCH)
        for arguments in ("api repos/OWNER/REPO -H Accept:application/json",
                          "api --hostname github.example repos/OWNER/REPO",
                          "api GraphQL", "api https://api.github.com/repos/OWNER/REPO"):
            for tool_name in SHELL_TOOL_NAMES:
                with self.subTest(arguments=arguments, tool=tool_name):
                    result = self.hook.run_hook(tool_name, WRAPPER + arguments)
                    self.assertNotEqual(self.hook.decision(result), "allow")


if __name__ == "__main__":
    unittest.main()
