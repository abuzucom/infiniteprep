#!/usr/bin/env python3
"""Pin plan-mode access on an invalid branch (issue 100).

Plan mode forbids the branch rename. The branch hook therefore permits plan
entry, plan exit, tool schema loading, and plan-file writes outside the
repository while recovery stays pending. Repository reads and writes stay
blocked. The Stop check defers the rename demand while plan mode holds.
"""
import json
import unittest

from tests.read_only_branch_support import BranchFixture, decision

INVALID_BRANCH = "not-a-valid-branch"
PLAN_EXIT_TOOLS = ("EnterPlanMode", "ExitPlanMode", "ToolSearch")
# These pass only while permission_mode is plan. EnterPlanMode passes in
# every mode, or a session could never reach plan mode.
PLAN_ONLY_TOOLS = ("ExitPlanMode", "ToolSearch")
PLAN_FILE_NAME = "plan.md"


class PlanModeInvalidBranchTest(unittest.TestCase):
    """An invalid branch keeps plan mode usable and repository access blocked."""

    def setUp(self):
        self.fixture = BranchFixture(self, INVALID_BRANCH)

    def test_plan_exit_tools_pass(self):
        for tool_name in PLAN_EXIT_TOOLS:
            with self.subTest(tool=tool_name):
                result = self.fixture.run(tool_name, {}, mode="plan")
                self.assertEqual(decision(result), "allow")

    def test_plan_tools_deny_outside_plan(self):
        for tool_name in PLAN_ONLY_TOOLS:
            with self.subTest(tool=tool_name):
                self.assertEqual(decision(self.fixture.run(tool_name, {})), "deny")

    def test_plan_entry_passes(self):
        self.assertEqual(decision(self.fixture.run("EnterPlanMode", {})), "allow")

    def test_external_plan_write_passes(self):
        plan_path = self.fixture.home / ".claude" / "plans" / PLAN_FILE_NAME
        result = self.fixture.run(
            "Write", {"file_path": str(plan_path), "content": "plan"}, mode="plan")
        self.assertEqual(decision(result), "allow")

    def test_repository_write_stays_denied(self):
        target = self.fixture.root / PLAN_FILE_NAME
        result = self.fixture.run(
            "Write", {"file_path": str(target), "content": "plan"}, mode="plan")
        self.assertEqual(decision(result), "deny")

    def test_repository_read_stays_denied(self):
        result = self.fixture.run(
            "Read", {"file_path": str(self.fixture.root / "README.md")}, mode="plan")
        self.assertEqual(decision(result), "deny")

    def stop(self, mode: str) -> dict:
        """Send one Stop event in `mode` and return the parsed stdout object."""
        result = self.fixture.run_payload({
            "hook_event_name": "Stop", "permission_mode": mode,
            "cwd": str(self.fixture.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def test_plan_mode_stop_reminds(self):
        output = self.stop("plan")
        self.assertNotEqual(output.get("decision"), "block")
        self.assertIn("git branch -m", json.dumps(output))

    def test_default_mode_stop_blocks(self):
        self.assertEqual(self.stop("default").get("decision"), "block")


if __name__ == "__main__":
    unittest.main()
