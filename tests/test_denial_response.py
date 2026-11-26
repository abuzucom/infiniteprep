#!/usr/bin/env python3
"""Every gate denial ends with one fixed agent response (issue #102).

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"
POLICY_PATH = REPO_ROOT / "AGENTS.md"
COMMAND_GATE_PATH = HOOKS / "_command_execution_gate.py"
EXPECTED_RESPONSE = (
    "Stop. Report this denial to the active human. Do not retry a variant "
    "or propose a gate change."
)
AUTHORIZATION_SENTENCE = (
    "After a gate denial, report it to the active human. Never retry a "
    "variant or offer a gate change."
)
RETRY_WORD = "retry"
POLICY_DENIAL_EXIT_CODE = 2
SAMPLE_REASON = "sample reason"
IMPORT_FALLBACK_HOOKS = (
    "block_destructive_bash.py",
    "block_destructive_cmd.py",
    "block_destructive_powershell.py",
    "block_infrastructure_access.py",
    "enforce_branch_name.py",
    "enforce_gate_adoption.py",
    "enforce_git_identity.py",
    "require_consent.py",
)
JSON_FALLBACK_HOOKS = frozenset({
    "block_destructive_bash.py",
    "block_destructive_powershell.py",
    "require_consent.py",
})


def load_hook(file_name: str):
    """Import one hook by path, since hooks/ is not an importable package."""
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    module_name = f"denial_response_{Path(file_name).stem}"
    spec = importlib.util.spec_from_file_location(module_name, HOOKS / file_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture(function, *arguments) -> tuple:
    """Call a function and return its result, stdout, and stderr."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = function(*arguments)
    return result, stdout.getvalue(), stderr.getvalue()


def json_reason(stdout: str) -> str:
    """Return the deny reason from either client output shape."""
    output = json.loads(stdout)
    specific = output.get("hookSpecificOutput")
    if isinstance(specific, dict):
        return specific["permissionDecisionReason"]
    return output["reason"]


class CoreResponseTest(unittest.TestCase):
    """The shared core appends the response to deny output only."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.core = load_hook("_gate_core.py")

    def test_constant_text(self) -> None:
        self.assertEqual(self.core.DENIAL_RESPONSE, EXPECTED_RESPONSE)

    def test_emit_deny_ends_with_response(self) -> None:
        code, stdout, stderr = capture(self.core.emit, "sample.py", "deny", SAMPLE_REASON)
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertTrue(json_reason(stdout).endswith(EXPECTED_RESPONSE), stdout)
        self.assertTrue(stderr.strip().endswith(EXPECTED_RESPONSE), stderr)

    def test_emit_ask_has_no_response(self) -> None:
        _, stdout, _ = capture(self.core.emit, "sample.py", "ask", SAMPLE_REASON)
        self.assertNotIn(EXPECTED_RESPONSE, json_reason(stdout))

    def test_unattended_ask_denies(self) -> None:
        _, stdout, stderr = capture(self.core.decide, "sample.py", {}, "ask", SAMPLE_REASON)
        self.assertTrue(json_reason(stdout).endswith(EXPECTED_RESPONSE), stdout)
        self.assertTrue(stderr.strip().endswith(EXPECTED_RESPONSE), stderr)


class ConsentHookResponseTest(unittest.TestCase):
    """The file-tool consent hook appends the response to deny output only."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = load_hook("require_consent.py")

    def test_deny_ends_with_response(self) -> None:
        code, stdout, stderr = capture(self.hook.emit, "deny", SAMPLE_REASON)
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertTrue(json_reason(stdout).endswith(EXPECTED_RESPONSE), stdout)
        self.assertTrue(stderr.strip().endswith(EXPECTED_RESPONSE), stderr)

    def test_ask_has_no_response(self) -> None:
        _, stdout, _ = capture(self.hook.emit, "ask", SAMPLE_REASON)
        self.assertNotIn(EXPECTED_RESPONSE, json_reason(stdout))


class BranchHookResponseTest(unittest.TestCase):
    """The branch hook appends the response for every client shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = load_hook("enforce_branch_name.py")

    def test_json_clients(self) -> None:
        for client in ("gemini", "antigravity"):
            with self.subTest(client=client):
                _, stdout, _ = capture(self.hook._deny, client, SAMPLE_REASON)
                self.assertTrue(json_reason(stdout).endswith(EXPECTED_RESPONSE), stdout)

    def test_stderr_client(self) -> None:
        code, _, stderr = capture(self.hook._deny, "claude", SAMPLE_REASON)
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertTrue(stderr.strip().endswith(EXPECTED_RESPONSE), stderr)


class AdoptionHookResponseTest(unittest.TestCase):
    """The adoption hook appends the response for every client shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = load_hook("enforce_gate_adoption.py")

    def test_json_clients(self) -> None:
        for client in ("gemini", "antigravity"):
            with self.subTest(client=client):
                _, stdout, _ = capture(self.hook._deny, client, SAMPLE_REASON)
                self.assertTrue(json_reason(stdout).endswith(EXPECTED_RESPONSE), stdout)

    def test_stderr_client(self) -> None:
        code, _, stderr = capture(self.hook._deny, "claude", SAMPLE_REASON)
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertTrue(stderr.strip().endswith(EXPECTED_RESPONSE), stderr)


class IdentityHookResponseTest(unittest.TestCase):
    """The identity hook appends the response to its stderr denials."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = load_hook("enforce_git_identity.py")

    def test_malformed_tool_input(self) -> None:
        payload = {"tool_name": "Bash", "tool_input": "not an object"}
        code, _, stderr = capture(self.hook._handle_pre_tool_use, payload, str(REPO_ROOT))
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertTrue(stderr.strip().endswith(EXPECTED_RESPONSE), stderr)

    def test_invocation_error(self) -> None:
        invocation = {"label": "git commit", "error": "sample error"}
        blocked, _, stderr = capture(self.hook._blocks_invocation, str(REPO_ROOT), invocation)
        self.assertTrue(blocked)
        self.assertTrue(stderr.strip().endswith(EXPECTED_RESPONSE), stderr)


class ImportFallbackResponseTest(unittest.TestCase):
    """A hook without its shared modules still ends with the response."""

    def run_isolated(self, file_name: str) -> subprocess.CompletedProcess:
        """Run one hook copy in a directory without its shared modules."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name) / file_name
        shutil.copy(HOOKS / file_name, target)
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        return subprocess.run(
            [sys.executable, str(target)],
            input="{}",
            capture_output=True,
            text=True,
            env=environment,
            cwd=temporary.name,
            check=False,
        )

    def test_every_fallback(self) -> None:
        for file_name in IMPORT_FALLBACK_HOOKS:
            with self.subTest(hook=file_name):
                result = self.run_isolated(file_name)
                self.assertEqual(result.returncode, POLICY_DENIAL_EXIT_CODE, result.stderr)
                self.assertTrue(result.stderr.strip().endswith(EXPECTED_RESPONSE), result.stderr)
                if file_name in JSON_FALLBACK_HOOKS:
                    reason = json_reason(result.stdout)
                    self.assertTrue(reason.endswith(EXPECTED_RESPONSE), reason)


class ReinjectionFailureResponseTest(unittest.TestCase):
    """A policy reinjection failure ends with the response."""

    def test_malformed_payload(self) -> None:
        environment = dict(os.environ)
        environment["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
        result = subprocess.run(
            [sys.executable, str(HOOKS / "reinject_agents_policy.py"), "--client", "claude"],
            input="[]",
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, POLICY_DENIAL_EXIT_CODE, result.stderr)
        self.assertTrue(result.stderr.strip().endswith(EXPECTED_RESPONSE), result.stderr)


class CommandGateWordingTest(unittest.TestCase):
    """No command gate message tells the agent to retry."""

    def test_no_string_mentions_retry(self) -> None:
        tree = ast.parse(COMMAND_GATE_PATH.read_text(encoding="utf-8"))
        offending = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and RETRY_WORD in node.value.lower()
        ]
        self.assertEqual(offending, [])


class PolicySentenceTest(unittest.TestCase):
    """AGENTS.md states the fixed response under Authorization."""

    def test_sentence_present(self) -> None:
        policy = " ".join(POLICY_PATH.read_text(encoding="utf-8").split())
        authorization = policy.split("### Authorization", 1)[1].split("### Precedence", 1)[0]
        self.assertIn(AUTHORIZATION_SENTENCE, authorization)


if __name__ == "__main__":
    unittest.main()
