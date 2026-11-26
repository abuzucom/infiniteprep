#!/usr/bin/env python3
"""Cover the split of authorship checks between pr-checks and pr-authorship."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REUSABLE_WORKFLOW = ROOT / ".github" / "workflows" / "agents-compliance.yml"
SYNC_WORKFLOW = ROOT / ".github" / "workflows" / "sync-check.yml"
# pr-checks owns these authorship checkers. sync-check.yml must not repeat those checkers.
AUTHORSHIP_COMMANDS = (
    "python scripts/check_banned_agents.py",
    "python scripts/check_git_identity.py",
    "python scripts/check_commit_attribution.py",
)
COMMIT_REFERENCE_COMMAND = "python scripts/check_external_pr_refs.py"


def load_job(workflow_path: Path, job_name: str) -> dict:
    """Return one parsed job of a workflow file."""
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return workflow["jobs"][job_name]


def collect_run_commands(job: dict) -> list[str]:
    """Return the run text of every step in a job."""
    return [step.get("run", "") for step in job["steps"]]


def job_runs_command(job: dict, command: str) -> bool:
    """Return whether any step of a job runs the command."""
    return any(command in text for text in collect_run_commands(job))


class PrChecksAuthorshipTest(unittest.TestCase):
    """pr-checks runs every authorship checker on drafts and ready pull requests."""

    def setUp(self):
        self.job = load_job(REUSABLE_WORKFLOW, "pr-checks")

    def test_job_runs_authorship_checkers(self):
        for command in AUTHORSHIP_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(job_runs_command(self.job, command))


class PrAuthorshipDeduplicationTest(unittest.TestCase):
    """sync-check.yml keeps only the check that pr-checks lacks."""

    def setUp(self):
        self.job = load_job(SYNC_WORKFLOW, "pr-authorship")

    def test_job_omits_duplicate_checkers(self):
        for command in AUTHORSHIP_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(job_runs_command(self.job, command))

    def test_job_keeps_range_reference_check(self):
        commands = collect_run_commands(self.job)
        self.assertTrue(any(
            COMMIT_REFERENCE_COMMAND in text and "--base" in text
            for text in commands))


if __name__ == "__main__":
    unittest.main()
