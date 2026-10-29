"""Attribute program allowlist and workflow decisions to the command execution gate."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_enforce_branch_name import HOOK_PATH, REPO_ROOT

GATE_PATH = REPO_ROOT / "hooks" / "_command_execution_gate.py"
GATE_NAME = "hooks/_command_execution_gate.py"
BRANCH_GATE_NAME = "hooks/enforce_branch_name.py"
TIMEOUT_SECONDS = 15


def _load_gate_module():
    """Import the gate by path, since hooks/ is not an importable package."""
    sys.path.insert(0, str(GATE_PATH.parent))
    spec = importlib.util.spec_from_file_location("_command_execution_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommandGateHookTest(unittest.TestCase):
    """Run the registered branch hook and check which gate each decision names."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        admin = self.root / ".git"
        (admin / "objects").mkdir(parents=True)
        (admin / "refs" / "heads").mkdir(parents=True)
        (admin / "HEAD").write_text("ref: refs/heads/fix/example\n", encoding="utf-8")
        (admin / "config").write_text("[core]\nbare = false\n", encoding="utf-8")
        global_config = self.root / "global.config"
        global_config.write_text("", encoding="utf-8")
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("GIT_")}
        self.environment.update({"CLAUDE_PROJECT_DIR": str(self.root), "GIT_CONFIG_NOSYSTEM": "1",
                                 "GIT_CONFIG_GLOBAL": str(global_config)})
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "run_tests.py").write_bytes(
            (REPO_ROOT / "scripts" / "run_tests.py").read_bytes())

    def run_candidate(self, command: str, client: str = "claude") -> subprocess.CompletedProcess:
        """Send one command payload to the hook without executing the command."""
        payload = {"hook_event_name": "PreToolUse", "permission_mode": "default",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        return subprocess.run(
            [sys.executable, str(HOOK_PATH), "--client", client], input=json.dumps(payload),
            env=self.environment, capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, check=False,
        )

    def test_unlisted_program_denial_names_the_command_gate(self) -> None:
        for command in ("node script.js", "python -c 'print(1)'", "npx vitest"):
            with self.subTest(command=command):
                result = self.run_candidate(command)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(f"blocked by {GATE_NAME}: Opaque command execution", result.stderr)
                self.assertNotIn(BRANCH_GATE_NAME, result.stderr)

    def test_wrapper_and_expansion_denials_name_the_command_gate(self) -> None:
        for command, reason in (("sudo git status", "wrapper"), ("echo $value", "unresolved")):
            with self.subTest(command=command):
                result = self.run_candidate(command)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(f"blocked by {GATE_NAME}:", result.stderr)
                self.assertIn(reason, result.stderr)

    def test_workflow_consent_names_the_command_gate(self) -> None:
        result = self.run_candidate("python scripts/run_tests.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "ask")
        self.assertIn(f"blocked by {GATE_NAME}:", output["permissionDecisionReason"])

    def test_codex_workflow_denial_names_the_command_gate(self) -> None:
        result = self.run_candidate("python scripts/run_tests.py", client="codex")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(f"blocked by {GATE_NAME}:", result.stderr)
        self.assertIn("execution consent", result.stderr)

    def test_branch_target_denial_still_names_the_branch_gate(self) -> None:
        result = self.run_candidate("git push origin HEAD:claude/x")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(f"blocked by {BRANCH_GATE_NAME}:", result.stderr)
        self.assertNotIn(GATE_NAME, result.stderr)


class CommandGateModuleTest(unittest.TestCase):
    """Classify command segments through the module functions directly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def test_segment_program_reason_classifies_programs(self) -> None:
        self.assertEqual(self.gate.segment_program_reason(["git", "status"]), "")
        self.assertEqual(self.gate.segment_program_reason(["echo", "hello"]), "")
        self.assertEqual(self.gate.segment_program_reason([">", "output"]), "")
        self.assertIn("inspectable operation", self.gate.segment_program_reason(["node", "x.js"]))
        self.assertIn("cannot claim", self.gate.segment_program_reason(["./git", "status"]))
        self.assertIn("wrapper", self.gate.segment_program_reason(["sudo", "git", "status"]))
        self.assertIn("wrapper", self.gate.segment_program_reason(["env", "-S", "'"]))

    def test_arguments_reason_rejects_unresolved_expansion(self) -> None:
        self.assertEqual(self.gate.arguments_reason(["echo", "hello"]), "")
        self.assertIn("unresolved", self.gate.arguments_reason(["echo", "$value"]))

    def test_workflow_needs_consent_matches_fixed_invocations(self) -> None:
        self.assertTrue(self.gate.workflow_needs_consent("make lint PYTHON=python", str(REPO_ROOT)))
        self.assertTrue(self.gate.workflow_needs_consent("rg -n branch README.md", str(REPO_ROOT)))
        self.assertFalse(self.gate.workflow_needs_consent("make lint SHELL=sh", str(REPO_ROOT)))
        self.assertFalse(self.gate.workflow_needs_consent("python other.py", str(REPO_ROOT)))

    def test_read_only_workflow_excludes_writing_workflows(self) -> None:
        self.assertTrue(self.gate.read_only_workflow("python scripts/sync.py --check", str(REPO_ROOT)))
        self.assertFalse(self.gate.read_only_workflow("python scripts/sync.py", str(REPO_ROOT)))
        self.assertFalse(self.gate.read_only_workflow("make lint PYTHON=python", str(REPO_ROOT)))


if __name__ == "__main__":
    unittest.main()
