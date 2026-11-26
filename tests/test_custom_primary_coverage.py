#!/usr/bin/env python3
"""CI and the branch checker cover a primary branch named in hooks/primary-branch.txt.

Workflow branch filters are static and cannot read the override file. The
workflows run on every pull request base. The gate integrity job rejects a
base outside the stable set that the trusted base checkout defines.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import check_branch_name

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
FILTERED_WORKFLOWS = (
    "agents-md-compliance.yml", "gate-integrity.yml",
    "immutable-conflict-check.yml", "sync-check.yml",
)
BRANCH_FILTER_KEYS = ("branches", "branches-ignore")
PR_EVENTS = ("pull_request", "pull_request_target")
BASE_CHECK_SCRIPT = ROOT / "scripts" / "check_pr_base.py"
BASE_CHECK_STEP = "Check pull request base"
SCRIPT_TIMEOUT_SECONDS = 30
FAILURE_EXIT_CODE = 1


def load_workflow(name: str) -> dict:
    """Return one parsed workflow."""
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def workflow_triggers(workflow: dict) -> dict:
    """Return the trigger mapping. PyYAML 1.1 reads a bare `on` key as True."""
    return workflow["on"] if "on" in workflow else workflow[True]


def make_repository(test_case: unittest.TestCase, primary: str = "", raw: bytes = b"") -> Path:
    """Create a repository root with hooks copied and an optional override."""
    temporary = tempfile.mkdtemp()
    test_case.addCleanup(shutil.rmtree, temporary)
    root = Path(temporary)
    (root / ".git").mkdir()
    hooks = root / "hooks"
    hooks.mkdir()
    for source in (ROOT / "hooks").glob("*.py"):
        (hooks / source.name).write_bytes(source.read_bytes())
    for source in (ROOT / "hooks").glob("*.txt"):
        (hooks / source.name).write_bytes(source.read_bytes())
    content = raw or (f"{primary}\n".encode("ascii") if primary else b"")
    if content:
        (hooks / "primary-branch.txt").write_bytes(content)
    return root


def run_base_check(root: Path, base: str) -> subprocess.CompletedProcess:
    """Run the base check script against one repository root."""
    return subprocess.run(
        [sys.executable, str(BASE_CHECK_SCRIPT), "--base", base, "--root", str(root)],
        capture_output=True, text=True, check=False, timeout=SCRIPT_TIMEOUT_SECONDS)


class WorkflowFilterTest(unittest.TestCase):
    """The four workflows run on every pull request base."""

    def test_base_change_reruns_workflows(self):
        # A base branch change fires the edited activity on the pull request.
        for name in FILTERED_WORKFLOWS:
            triggers = workflow_triggers(load_workflow(name))
            event = next(event for event in PR_EVENTS if event in triggers)
            with self.subTest(workflow=name):
                self.assertIn("edited", triggers[event]["types"])

    def test_workflows_lack_branch_filter(self):
        for name in FILTERED_WORKFLOWS:
            triggers = workflow_triggers(load_workflow(name))
            for event in PR_EVENTS:
                for key in BRANCH_FILTER_KEYS:
                    with self.subTest(workflow=name, event=event, key=key):
                        self.assertNotIn(key, triggers.get(event) or {})


class GateIntegrityStepTest(unittest.TestCase):
    """The gate integrity job runs the trusted base check script."""

    def base_check_step(self) -> dict:
        steps = load_workflow("gate-integrity.yml")["jobs"]["protected-gate-changes"]["steps"]
        matching = [step for step in steps if step.get("name") == BASE_CHECK_STEP]
        self.assertEqual(len(matching), 1)
        return matching[0]

    def test_base_step_runs_trusted_script(self):
        step = self.base_check_step()
        self.assertEqual(step["working-directory"], "trusted-base")
        self.assertIn("scripts/check_pr_base.py", step["run"])

    def test_base_change_reruns_check(self):
        # A base branch change fires the edited activity.
        triggers = workflow_triggers(load_workflow("gate-integrity.yml"))
        self.assertIn("edited", triggers["pull_request"]["types"])

    def test_base_ref_stays_in_env(self):
        step = self.base_check_step()
        self.assertEqual(step["env"]["PR_BASE_REF"], "${{ github.event.pull_request.base.ref }}")
        self.assertNotIn("${{", step["run"])

    def run_fallback(self, labels_json: str) -> subprocess.CompletedProcess:
        """Run the step script in a base checkout that lacks check_pr_base.py."""
        workspace = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workspace)
        # The step calls `python`. A link keeps the test independent of PATH names.
        tools = Path(workspace) / "bin"
        tools.mkdir()
        (tools / "python").symlink_to(sys.executable)
        base = Path(workspace) / "trusted-base"
        base.mkdir()
        environment = {"PATH": str(tools), "PR_BASE_REF": "main", "PR_LABELS_JSON": labels_json}
        return subprocess.run(
            ["/bin/bash", "-e", "-c", self.base_check_step()["run"]], cwd=base, env=environment,
            capture_output=True, text=True, check=False, timeout=SCRIPT_TIMEOUT_SECONDS)

    @unittest.skipIf(sys.platform == "win32", "The workflow step runs under bash on Linux")
    def test_fallback_requires_approval_label(self):
        self.assertEqual(self.run_fallback('["gate-change-approved"]').returncode, 0)
        denied = self.run_fallback('["other"]')
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("gate-change-approved", denied.stderr)


class BaseCheckScriptTest(unittest.TestCase):
    """check_pr_base.py accepts stable bases and rejects every other base."""

    def test_default_stable_bases_pass(self):
        root = make_repository(self)
        for base in ("main", "test", "develop"):
            with self.subTest(base=base):
                result = run_base_check(root, base)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_override_primary_base_passes(self):
        result = run_base_check(make_repository(self, "trunk"), "trunk")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_other_base_fails_closed(self):
        root = make_repository(self, "trunk")
        for base in ("main", "feature/x", "staging"):
            with self.subTest(base=base):
                self.assertEqual(run_base_check(root, base).returncode, FAILURE_EXIT_CODE)

    def test_unusable_override_fails_closed(self):
        root = make_repository(self, raw=b"a b\nc\n")
        self.assertEqual(run_base_check(root, "main").returncode, FAILURE_EXIT_CODE)


class CheckerOverrideTest(unittest.TestCase):
    """The portable checker exempts the override branch outside strict mode."""

    def run_checker(self, root: Path, branch: str, *extra: str) -> subprocess.CompletedProcess:
        script = ROOT / "scripts" / "check_branch_name.py"
        return subprocess.run(
            [sys.executable, str(script), branch, "--repo", str(root), *extra],
            capture_output=True, text=True, check=False, timeout=SCRIPT_TIMEOUT_SECONDS)

    def test_checker_exempts_override_primary(self):
        self.assertEqual(self.run_checker(make_repository(self, "trunk"), "trunk").returncode, 0)

    def test_strict_rejects_override_primary(self):
        result = self.run_checker(make_repository(self, "trunk"), "trunk", "--strict-agent-preflight")
        self.assertEqual(result.returncode, FAILURE_EXIT_CODE)

    def test_checker_unusable_override_fails(self):
        result = self.run_checker(make_repository(self, raw=b"a b\nc\n"), "main")
        self.assertEqual(result.returncode, FAILURE_EXIT_CODE)
        self.assertIn("primary-branch.txt", result.stderr)

    def test_find_violations_primary_keyword(self):
        self.assertEqual(check_branch_name.find_violations("trunk", primary="trunk"), [])
        self.assertNotEqual(check_branch_name.find_violations("trunk"), [])


if __name__ == "__main__":
    unittest.main()
