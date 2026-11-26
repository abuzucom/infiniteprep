"""A newer pull request commit replaces the older security-devops scan."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "security-devops.yml"
)
PULL_REQUEST_ONLY = "${{ github.event_name == 'pull_request' }}"
PULL_REQUEST_NUMBER = "github.event.pull_request.number"
COMMIT_SHA = "github.sha"
COMMENT_JOB = "scan-comment"


class SecurityDevopsConcurrencyTest(unittest.TestCase):
    """Superseded pull request scans stop. Every main scan finishes its upload."""

    workflow: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once."""
        cls.workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_only_pull_request_runs_cancel(self) -> None:
        """A push to main always completes, so the Security tab gets every upload."""
        self.assertEqual(self.workflow["concurrency"]["cancel-in-progress"], PULL_REQUEST_ONLY)

    def test_group_separates_prs_and_commits(self) -> None:
        """One group per pull request, and one group per pushed commit."""
        group = self.workflow["concurrency"]["group"]
        self.assertIn(PULL_REQUEST_NUMBER, group)
        self.assertIn(COMMIT_SHA, group)
        self.assertLess(group.index(PULL_REQUEST_NUMBER), group.index(COMMIT_SHA))

    def test_comment_job_keeps_own_group(self) -> None:
        """The comment job still serializes its own writes per pull request."""
        job_concurrency = self.workflow["jobs"][COMMENT_JOB]["concurrency"]
        self.assertTrue(job_concurrency["cancel-in-progress"])
        self.assertIn(PULL_REQUEST_NUMBER, job_concurrency["group"])


if __name__ == "__main__":
    unittest.main()
