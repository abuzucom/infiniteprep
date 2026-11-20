"""Cover the workflow wiring that keeps pull request checks off a push to main.

A merged commit already passed every check on its pull request. The ci,
cross-platform, and AGENTS.md compliance workflows trigger on pull_request
only. The sync check job runs on pull requests only. A push to main does not
run them.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
WORKFLOWS: Final[Path] = ROOT / ".github" / "workflows"
PULL_REQUEST_EVENT: Final[str] = "github.event_name == 'pull_request'"
PULL_REQUEST_ONLY_WORKFLOWS: Final[tuple[str, ...]] = (
    "ci.yml",
    "os-tests.yml",
    "agents-md-compliance.yml",
)
COMPLIANCE_TYPES: Final[list[str]] = ["opened", "synchronize", "reopened", "ready_for_review"]


def load_workflow(workflow_name: str) -> dict[Any, Any]:
    """Return the parsed workflow, failing when the file is absent."""
    path = WORKFLOWS / workflow_name
    if not path.is_file():
        raise AssertionError(f"missing workflow: {workflow_name}")
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def read_triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    """Return the trigger map. YAML 1.1 parses a bare `on` key as True."""
    return dict(workflow.get("on") or workflow.get(True) or {})


class PullRequestOnlyWorkflowTest(unittest.TestCase):
    """Each named workflow starts on pull requests and never on a push."""

    def test_workflow_has_no_push_trigger(self) -> None:
        for workflow_name in PULL_REQUEST_ONLY_WORKFLOWS:
            with self.subTest(workflow=workflow_name):
                triggers = read_triggers(load_workflow(workflow_name))
                self.assertNotIn("push", triggers)
                self.assertIn("pull_request", triggers)


class SyncCheckJobTest(unittest.TestCase):
    """The sync check job runs on pull requests only."""

    def test_check_sync_runs_only_for_pull_requests(self) -> None:
        job = load_workflow("sync-check.yml")["jobs"]["check-sync"]
        self.assertEqual(job.get("if"), PULL_REQUEST_EVENT)


class ComplianceTriggerTest(unittest.TestCase):
    """The AGENTS.md compliance workflow runs on pull requests to main."""

    triggers: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the compliance workflow once. No test changes the parsed data."""
        cls.triggers = read_triggers(load_workflow("agents-md-compliance.yml"))

    def test_pull_request_trigger_targets_main(self) -> None:
        self.assertEqual(self.triggers["pull_request"]["branches"], ["main"])

    def test_ready_for_review_starts_the_non_draft_checks(self) -> None:
        self.assertEqual(self.triggers["pull_request"]["types"], COMPLIANCE_TYPES)


if __name__ == "__main__":
    unittest.main()
