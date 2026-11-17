"""Cover the workflow wiring that skips pull request checks on a push to main.

The lint jobs and the sync check run on pull requests only. A push to main
does not run them. The AGENTS.md compliance workflow triggers on
pull_request only.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
WORKFLOWS: Final[Path] = ROOT / ".github" / "workflows"
PULL_REQUEST_EVENT: Final[str] = "github.event_name == 'pull_request'"
PUSH_EVENT: Final[str] = "github.event_name == 'push'"
LINT_SUCCEEDED: Final[str] = "needs.lint-and-style.result == 'success'"
PULL_REQUEST_ONLY_JOBS: Final[tuple[tuple[str, str], ...]] = (
    ("ci.yml", "lint-and-style"),
    ("ci.yml", "agents-lint"),
    ("sync-check.yml", "check-sync"),
)
COMPLIANCE_TYPES: Final[list[str]] = ["opened", "synchronize", "reopened", "ready_for_review"]


def load_workflow(workflow_name: str) -> dict[str, Any]:
    """Return the parsed workflow, failing when the file is absent."""
    path = WORKFLOWS / workflow_name
    if not path.is_file():
        raise AssertionError(f"missing workflow: {workflow_name}")
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def read_triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return the trigger map. YAML 1.1 parses a bare `on` key as True."""
    return dict(workflow.get("on") or workflow.get(True) or {})


class PullRequestOnlyJobTest(unittest.TestCase):
    """Each named job runs on pull requests and skips on a push to main."""

    def test_job_runs_only_for_pull_requests(self) -> None:
        for workflow_name, job_name in PULL_REQUEST_ONLY_JOBS:
            with self.subTest(workflow=workflow_name, job=job_name):
                job = load_workflow(workflow_name)["jobs"][job_name]
                self.assertEqual(job.get("if"), PULL_REQUEST_EVENT)


class TestMatrixOnPushTest(unittest.TestCase):
    """The test matrix still runs on main after the lint job skips."""

    def test_push_runs_the_matrix_without_a_lint_result(self) -> None:
        condition = load_workflow("ci.yml")["jobs"]["test-matrix"]["if"]
        self.assertIn(f"({LINT_SUCCEEDED} || {PUSH_EVENT})", condition)


class ComplianceTriggerTest(unittest.TestCase):
    """The AGENTS.md compliance workflow runs on pull requests to main."""

    def setUp(self) -> None:
        self.triggers = read_triggers(load_workflow("agents-md-compliance.yml"))

    def test_push_trigger_is_absent(self) -> None:
        self.assertNotIn("push", self.triggers)

    def test_pull_request_trigger_targets_main(self) -> None:
        self.assertEqual(self.triggers["pull_request"]["branches"], ["main"])

    def test_ready_for_review_starts_the_non_draft_checks(self) -> None:
        self.assertEqual(self.triggers["pull_request"]["types"], COMPLIANCE_TYPES)


if __name__ == "__main__":
    unittest.main()
