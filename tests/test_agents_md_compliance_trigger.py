#!/usr/bin/env python3
"""Cover the triggers limited to pull requests in the compliance and sync workflows."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "agents-md-compliance.yml"
# The reusable workflow runs pr-checks on drafts. ready_for_review reruns it
# when a draft becomes ready. edited reruns it after a base change.
EXPECTED_TYPES = ["opened", "synchronize", "reopened", "ready_for_review", "edited"]
SYNC_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "sync-check.yml"
# The edited activity reruns the pull request message checks.
SYNC_EXPECTED_TYPES = ["opened", "synchronize", "reopened", "ready_for_review", "edited"]


def load_trigger_block(text: str) -> dict:
    """Return the parsed top-level trigger mapping of a workflow."""
    workflow = yaml.safe_load(text)
    # PyYAML 1.1 parses a bare `on:` key as the boolean True.
    if "on" in workflow:
        return workflow["on"]
    return workflow[True]


class AgentsMdComplianceTriggerTest(unittest.TestCase):
    """Run compliance before merge instead of replaying it after merge."""

    def setUp(self):
        self.text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.triggers = load_trigger_block(self.text)

    def test_workflow_has_no_push_trigger(self):
        self.assertNotIn("push", self.triggers)

    def test_workflow_pull_request_only(self):
        self.assertEqual(set(self.triggers), {"pull_request"})

    def test_pull_request_targets_main(self):
        # Gate integrity checks the base. A static filter cannot read the override.
        self.assertNotIn("branches", self.triggers["pull_request"])

    def test_pull_request_activity_types(self):
        self.assertEqual(
            self.triggers["pull_request"]["types"], EXPECTED_TYPES)

    def test_ready_for_review_commented(self):
        self.assertIn("ready_for_review", self.text)
        self.assertRegex(self.text, r"#[^\n]*draft")

    def test_reusable_workflow_called(self):
        workflow = yaml.safe_load(self.text)
        self.assertEqual(
            workflow["jobs"]["compliance"]["uses"],
            "./.github/workflows/agents-compliance.yml",
        )


class SyncCheckTriggerTest(unittest.TestCase):
    """Run the sync check on pull requests only, never again after merge."""

    def setUp(self):
        self.triggers = load_trigger_block(
            SYNC_WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_sync_check_no_push(self):
        self.assertNotIn("push", self.triggers)

    def test_sync_check_pull_request_only(self):
        self.assertEqual(set(self.triggers), {"pull_request"})

    def test_sync_check_trigger_unchanged(self):
        # Gate integrity checks the base. A static filter cannot read the override.
        self.assertNotIn("branches", self.triggers["pull_request"])
        self.assertEqual(
            self.triggers["pull_request"]["types"], SYNC_EXPECTED_TYPES)


if __name__ == "__main__":
    unittest.main()
