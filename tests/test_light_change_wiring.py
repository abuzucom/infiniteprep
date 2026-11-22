"""Behavioral checks for the workflow wiring that skips heavy work on light changes."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"
CLASSIFY_COMMAND = 'python3 scripts/classify_changed_paths.py --base "$BASE_SHA" --head "$HEAD_SHA"'
# A pull-request-only workflow always has the pull request revisions.
PULL_REQUEST_REVISION_SOURCES = {
    "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
    "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
}
# A workflow that also runs on a push compares the pushed commit with the previous one.
PUSH_REVISION_SOURCES = {
    "BASE_SHA": "${{ github.event.pull_request.base.sha || github.event.before }}",
    "HEAD_SHA": "${{ github.event.pull_request.head.sha || github.sha }}",
}
REVISION_SOURCES_BY_WORKFLOW = {
    "ci.yml": PULL_REQUEST_REVISION_SOURCES,
    "security-devops.yml": PUSH_REVISION_SOURCES,
    "dependency-review.yml": PULL_REQUEST_REVISION_SOURCES,
}
LIGHT_OUTPUT = "${{ steps.classify.outputs.light }}"
NOT_CANCELLED = "!cancelled()"
JOB_NOT_LIGHT = "needs.changes.outputs.light != 'true'"
STEP_NOT_LIGHT = "steps.classify.outputs.light != 'true'"
LINT_SUCCEEDED = "needs.lint-and-style.result == 'success'"
# Each entry names a workflow and the job that holds the classify step.
CLASSIFYING_JOBS = (
    ("ci.yml", "changes"),
    ("security-devops.yml", "msdo"),
    ("dependency-review.yml", "dependency-review"),
)
# The scan jobs classify in their own steps, so each keeps a single checkout.
SCAN_JOBS = CLASSIFYING_JOBS[1:]
ALWAYS_RUNNING_CI_JOBS = ("changes", "checks", "lint-and-style", "agents-lint")


def load_jobs(workflow_name: str) -> dict[str, Any]:
    """Return the parsed jobs of one workflow."""
    text = (WORKFLOWS / workflow_name).read_text(encoding="utf-8")
    return dict(yaml.safe_load(text)["jobs"])


def load_jobs_by_workflow(workflow_jobs: tuple[tuple[str, str], ...]) -> dict[str, dict[str, Any]]:
    """Return the parsed jobs of each named workflow, keyed by workflow file name."""
    return {workflow_name: load_jobs(workflow_name) for workflow_name, _job in workflow_jobs}


def find_classify_index(steps: list[dict[str, Any]]) -> int:
    """Return the position of the classify step."""
    return next(index for index, step in enumerate(steps) if step.get("id") == "classify")


class ClassifyStepTest(unittest.TestCase):
    """Each workflow classifies the change with the repository script."""

    jobs_by_workflow: dict[str, dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse each classifying workflow once. No test changes the parsed data."""
        cls.jobs_by_workflow = load_jobs_by_workflow(CLASSIFYING_JOBS)

    def test_checkout_fetches_the_whole_history(self) -> None:
        """The merge base lookup needs the complete history and no credentials."""
        for workflow_name, job_name in CLASSIFYING_JOBS:
            with self.subTest(workflow=workflow_name):
                checkout = self.jobs_by_workflow[workflow_name][job_name]["steps"][0]
                self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
                self.assertEqual(checkout["with"]["fetch-depth"], 0)
                self.assertIs(checkout["with"]["persist-credentials"], False)

    def test_classify_revisions_come_from_env(self) -> None:
        """Event data reaches the script as environment values, never as shell text."""
        for workflow_name, job_name in CLASSIFYING_JOBS:
            with self.subTest(workflow=workflow_name):
                steps = self.jobs_by_workflow[workflow_name][job_name]["steps"]
                classify = steps[find_classify_index(steps)]
                self.assertEqual(classify["run"], CLASSIFY_COMMAND)
                self.assertEqual(classify["env"], REVISION_SOURCES_BY_WORKFLOW[workflow_name])


class TestMatrixSkipTest(unittest.TestCase):
    """The ci test matrix skips on a light change and runs in every other case."""

    jobs: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the ci jobs once. No test changes the parsed data."""
        cls.jobs = load_jobs("ci.yml")

    def test_changes_job_exports_the_light_flag(self) -> None:
        """The job output reads the light flag of the classify step."""
        self.assertEqual(self.jobs["changes"]["outputs"], {"light": LIGHT_OUTPUT})

    def test_test_matrix_runs_unless_the_class_is_light(self) -> None:
        """A failed or skipped changes job yields no flag, and the matrix runs."""
        job = self.jobs["test-matrix"]
        self.assertEqual(job["needs"], ["changes", "lint-and-style"])
        self.assertIn(NOT_CANCELLED, job["if"])
        self.assertIn(JOB_NOT_LIGHT, job["if"])

    def test_test_matrix_still_requires_a_clean_lint(self) -> None:
        """The status function replaces the implicit success check, so lint is explicit."""
        self.assertIn(LINT_SUCCEEDED, self.jobs["test-matrix"]["if"])

    def test_cheap_ci_jobs_always_run(self) -> None:
        """The classifier, the wiring tests, and both lint jobs carry no condition."""
        for job_name in ALWAYS_RUNNING_CI_JOBS:
            with self.subTest(job=job_name):
                self.assertNotIn("if", self.jobs[job_name])


class ScanSkipTest(unittest.TestCase):
    """Each scan job always reports, and its scan steps skip on a light change."""

    jobs_by_workflow: dict[str, dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse each scan workflow once. No test changes the parsed data."""
        cls.jobs_by_workflow = load_jobs_by_workflow(SCAN_JOBS)

    def test_scan_jobs_carry_no_job_condition(self) -> None:
        """The job runs on every change, so its check always reports a result."""
        for workflow_name, job_name in SCAN_JOBS:
            with self.subTest(workflow=workflow_name):
                job = self.jobs_by_workflow[workflow_name][job_name]
                self.assertNotIn("if", job)
                self.assertNotIn("needs", job)

    def test_crashed_classification_fails_the_scan_job(self) -> None:
        """A crashed classify step stops the job with a failure, never with a silent skip."""
        for workflow_name, job_name in SCAN_JOBS:
            with self.subTest(workflow=workflow_name):
                steps = self.jobs_by_workflow[workflow_name][job_name]["steps"]
                classify = steps[find_classify_index(steps)]
                self.assertNotIn("continue-on-error", classify)
                self.assertNotIn("if", classify)

    def test_later_steps_skip_light_changes(self) -> None:
        """The scan and its upload carry the light condition."""
        for workflow_name, job_name in SCAN_JOBS:
            steps = self.jobs_by_workflow[workflow_name][job_name]["steps"]
            later_steps = steps[find_classify_index(steps) + 1 :]
            self.assertTrue(later_steps, workflow_name)
            for step in later_steps:
                with self.subTest(workflow=workflow_name, step=step["name"]):
                    self.assertEqual(step["if"], STEP_NOT_LIGHT)


if __name__ == "__main__":
    unittest.main()
