#!/usr/bin/env python3
"""Cover the draft pull request reach of the reusable pr-checks job."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "agents-compliance.yml"
PULL_REQUEST_CONDITION = "github.event_name == 'pull_request'"
PROSE_GATE_COMMAND = "python scripts/check_agent_prose_gate.py"


def load_pr_checks_job() -> dict:
    """Return the parsed pr-checks job of the reusable compliance workflow."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"]["pr-checks"]


class PrChecksDraftCoverageTest(unittest.TestCase):
    """Run pr-checks on drafts because Rule 5 makes agent pull requests drafts."""

    def setUp(self):
        self.job = load_pr_checks_job()

    def test_condition_does_not_exclude_drafts(self):
        self.assertNotIn("draft", self.job["if"])

    def test_condition_requires_pull_request(self):
        self.assertIn(PULL_REQUEST_CONDITION, self.job["if"])

    def test_job_runs_prose_gate(self):
        step_commands = [step.get("run", "") for step in self.job["steps"]]
        self.assertTrue(
            any(PROSE_GATE_COMMAND in command for command in step_commands),
            "pr-checks must run scripts/check_agent_prose_gate.py",
        )


if __name__ == "__main__":
    unittest.main()
