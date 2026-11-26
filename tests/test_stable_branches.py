#!/usr/bin/env python3
"""The primary branch, test, and develop form the stable branch set.

Pull requests may target any stable branch. Agents treat every stable branch
as read-only. The develop branch is the staging branch. Staging gets no
separate support.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import check_branch_name
from tests.read_only_branch_support import BranchFixture, decision
from tests.test_command_execution_gate import _load_gate_module
from tests.test_enforce_branch_name import hook as branch_hook

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PR_BASE_HOOK_PATH = REPOSITORY_ROOT / "hooks" / "enforce_pr_base.py"
CORE = _load_gate_module().core
POLICY_DENIAL_EXIT_CODE = 2
HOOK_TIMEOUT_SECONDS = 15
EXTRA_STABLE = ("test", "develop")
NON_STABLE_BASES = ("staging", "release", "feature/x")
PR_EDIT = ["pr", "edit", "7"]
PR_CREATE = ["pr", "create", "--draft", "--title", "T"]


class StableRepositoryCase(unittest.TestCase):
    """Create an empty repository root for gate verdicts."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".git").mkdir()
        CORE.reset_invocation_caches()

    def write_primary(self, name: str) -> None:
        """Write hooks/primary-branch.txt with one branch name."""
        hooks = self.root / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "primary-branch.txt").write_text(f"{name}\n", encoding="utf-8")

    def verdict(self, args: list) -> str:
        """Return the forge gate decision for one gh command."""
        return CORE.forge_verdict("gh", args, str(self.root))[0]


class StableSetTest(StableRepositoryCase):
    """core.stable_branches returns the primary branch plus test and develop."""

    def test_stable_set_default_members(self):
        self.assertEqual(CORE.stable_branches(str(self.root)), frozenset(("main", *EXTRA_STABLE)))

    def test_stable_set_follows_override(self):
        self.write_primary("trunk")
        self.assertEqual(CORE.stable_branches(str(self.root)),
                         frozenset(("trunk", *EXTRA_STABLE)))

    def test_stable_set_primary_develop(self):
        self.write_primary("develop")
        self.assertEqual(CORE.stable_branches(str(self.root)), frozenset(("develop", "test")))

    def test_stable_set_unusable_override(self):
        self.write_primary("a b")
        with self.assertRaises(ValueError):
            CORE.stable_branches(str(self.root))


class StableBaseVerdictTest(StableRepositoryCase):
    """The shell gates accept every stable base and deny other bases."""

    def test_stable_base_not_denied(self):
        for base in ("main", *EXTRA_STABLE):
            for command in (PR_CREATE, PR_EDIT):
                with self.subTest(base=base, command=command):
                    self.assertNotEqual(self.verdict([*command, "--base", base]), "deny")

    def test_other_base_still_denied(self):
        for base in NON_STABLE_BASES:
            with self.subTest(base=base):
                self.assertEqual(self.verdict([*PR_EDIT, "--base", base]), "deny")

    def test_denial_names_stable_set(self):
        _decision, reason = CORE.pull_request_base_verdict([*PR_EDIT, "--base", "staging"],
                                                           str(self.root))
        for name in ("main", *EXTRA_STABLE):
            self.assertIn(name, reason)

    def test_override_keeps_extra_stable(self):
        self.write_primary("trunk")
        self.assertEqual(self.verdict([*PR_EDIT, "--base", "main"]), "deny")
        for base in ("trunk", *EXTRA_STABLE):
            with self.subTest(base=base):
                self.assertNotEqual(self.verdict([*PR_EDIT, "--base", base]), "deny")


def _run_pr_base_hook(project_dir: Path, base: str) -> subprocess.CompletedProcess:
    """Send one MCP pull request create call through the base hook."""
    environment = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    payload = {"hook_event_name": "PreToolUse", "tool_name": "mcp__github__create_pull_request",
               "tool_input": {"base": base, "title": "T"}}
    return subprocess.run([sys.executable, str(PR_BASE_HOOK_PATH)], input=json.dumps(payload),
                          capture_output=True, text=True, env=environment, check=False,
                          timeout=HOOK_TIMEOUT_SECONDS)


class McpStableBaseTest(StableRepositoryCase):
    """GitHub MCP pull request tools may target any stable branch."""

    def test_mcp_stable_base_passes(self):
        for base in EXTRA_STABLE:
            with self.subTest(base=base):
                self.assertEqual(_run_pr_base_hook(self.root, base).returncode, 0)

    def test_mcp_staging_base_denied(self):
        result = _run_pr_base_hook(self.root, "staging")
        self.assertEqual(result.returncode, POLICY_DENIAL_EXIT_CODE, result.stdout)


class StableBranchStateTest(unittest.TestCase):
    """The branch hook treats test and develop as read-only like the primary branch."""

    def test_branch_state_stable_read_only(self):
        for branch in EXTRA_STABLE:
            with self.subTest(branch=branch):
                fixture = BranchFixture(self, branch)
                self.assertEqual(branch_hook.branch_state(branch, str(fixture.root)), "read-only")

    def test_branch_state_staging_invalid(self):
        fixture = BranchFixture(self, "staging")
        self.assertEqual(branch_hook.branch_state("staging", str(fixture.root)), "invalid")

    def test_recovery_command_stable_switch(self):
        for branch in EXTRA_STABLE:
            with self.subTest(branch=branch):
                self.assertEqual(branch_hook.recovery_command(branch, False), "git switch -c")

    def test_stable_branch_allows_reads(self):
        fixture = BranchFixture(self, "develop")
        self.assertEqual(decision(fixture.shell("git status")), "allow")

    def test_stable_branch_denies_writes(self):
        fixture = BranchFixture(self, "test")
        self.assertEqual(decision(fixture.shell("touch notes.txt")), "deny")


class StableBranchNameTest(unittest.TestCase):
    """The branch name checker exempts stable branches outside strict preflight."""

    def test_checker_exempts_stable_branches(self):
        for branch in EXTRA_STABLE:
            with self.subTest(branch=branch):
                self.assertEqual(check_branch_name.find_violations(branch), [])

    def test_strict_preflight_rejects_stable(self):
        for branch in EXTRA_STABLE:
            with self.subTest(branch=branch):
                self.assertNotEqual(check_branch_name.find_violations(branch, strict=True), [])

    def test_checker_rejects_staging(self):
        self.assertNotEqual(check_branch_name.find_violations("staging"), [])


if __name__ == "__main__":
    unittest.main()
