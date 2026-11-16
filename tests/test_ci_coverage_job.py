#!/usr/bin/env python3
"""Cover the code coverage report in the ci workflow."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"
LOCKFILE = ROOT / "requirements-lock.txt"

UPLOAD_ARTIFACT = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
DOWNLOAD_ARTIFACT = "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"
GITHUB_SCRIPT = "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3"
COVERAGE_VERSION = "7.17.3"
COMMENT_MARKER = "infiniteprep-coverage"
SUMMARY_ARTIFACT = "coverage-summary"


def read_workflow_text(path: Path) -> str:
    """Return the workflow text, failing when the file is absent."""
    if not path.is_file():
        raise AssertionError(f"missing workflow: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def extract_job_block(workflow_text: str, job_name: str) -> str:
    """Return the text of one top-level job, up to the next job key."""
    job_header = re.compile(rf"^  {re.escape(job_name)}:\n", re.MULTILINE)
    header_match = job_header.search(workflow_text)
    if header_match is None:
        raise AssertionError(f"missing job: {job_name}")
    remainder = workflow_text[header_match.end() :]
    next_job = re.search(r"^  \S[^\n]*:\n", remainder, re.MULTILINE)
    return remainder if next_job is None else remainder[: next_job.start()]


def extract_permissions(job_text: str) -> dict[str, str]:
    """Return the permission names and values of one job."""
    permissions: dict[str, str] = {}
    in_block = False
    for line in job_text.splitlines():
        if re.match(r"^\s{4}permissions:\s*$", line):
            in_block = True
            continue
        if in_block:
            entry = re.match(r"^\s{6}([a-z-]+):\s*(\S+)\s*$", line)
            if entry is None:
                in_block = False
                continue
            permissions[entry.group(1)] = entry.group(2)
    return permissions


class CoverageMeasurementTest(unittest.TestCase):
    """Keep the coverage measurement inside the ubuntu test leg."""

    def setUp(self) -> None:
        self.text = read_workflow_text(CI_WORKFLOW)
        self.matrix = extract_job_block(self.text, "test-matrix")

    def test_the_ubuntu_leg_runs_the_suite_under_coverage(self) -> None:
        """The ubuntu leg runs the suite under coverage."""
        self.assertIn("coverage run -m pytest", self.matrix)
        self.assertIn("if: matrix.os == 'ubuntu-latest'", self.matrix)

    def test_the_other_legs_run_the_suite_without_coverage(self) -> None:
        """The other legs run the plain suite."""
        self.assertIn("if: matrix.os != 'ubuntu-latest'", self.matrix)

    def test_the_measurement_writes_the_report_and_the_summary(self) -> None:
        """The report and the job summary carry the coverage numbers."""
        self.assertIn("coverage report", self.matrix)
        self.assertIn("coverage xml", self.matrix)
        self.assertIn("coverage html", self.matrix)
        self.assertIn("GITHUB_STEP_SUMMARY", self.matrix)

    def test_the_summary_artifact_is_uploaded_with_a_pinned_action(self) -> None:
        """The summary artifact goes up through the pinned upload action."""
        self.assertIn(UPLOAD_ARTIFACT, self.matrix)
        self.assertIn(f"name: {SUMMARY_ARTIFACT}", self.matrix)

    def test_the_summary_document_comes_from_the_tested_script(self) -> None:
        """The bounded summary document comes from the tested builder script."""
        self.assertIn("scripts/build_coverage_summary.py", self.matrix)
        self.assertIn("coverage-summary.json", self.matrix)

    def test_the_report_never_fails_the_pull_request(self) -> None:
        """The coverage number reports and never blocks."""
        self.assertNotIn("--fail-under", self.text)

    def test_the_reporting_steps_never_fail_the_job(self) -> None:
        """A failure in the reporting leaves the pull request green."""
        self.assertIn("continue-on-error: true", self.matrix)

    def test_the_test_job_holds_no_write_permission(self) -> None:
        """The job that runs the suite holds no write permission."""
        permissions = extract_permissions(self.matrix)
        for name, value in permissions.items():
            self.assertNotEqual(value, "write", f"the test job holds {name}: write")

    def test_the_job_allows_time_for_the_measured_suite(self) -> None:
        """The job timeout covers the suite under coverage."""
        timeout = re.search(r"^\s{4}timeout-minutes:\s*(\d+)\s*$", self.matrix, re.MULTILINE)
        self.assertIsNotNone(timeout, "the matrix job holds no timeout")
        self.assertGreaterEqual(int(timeout.group(1)), 25)


class CoverageCommentJobTest(unittest.TestCase):
    """Keep the pull request comment job separate and least privilege."""

    def setUp(self) -> None:
        self.text = read_workflow_text(CI_WORKFLOW)
        self.job = extract_job_block(self.text, "coverage-comment")

    def test_the_job_writes_only_the_pull_request_comment(self) -> None:
        """The job holds the pull request write permission and nothing else."""
        permissions = extract_permissions(self.job)
        self.assertEqual(permissions.get("contents"), "read")
        self.assertEqual(permissions.get("pull-requests"), "write")

    def test_the_job_downloads_the_summary_with_a_pinned_action(self) -> None:
        """The summary arrives through the pinned download action."""
        self.assertIn(DOWNLOAD_ARTIFACT, self.job)
        self.assertIn(f"name: {SUMMARY_ARTIFACT}", self.job)

    def test_the_job_posts_the_comment_with_a_pinned_action(self) -> None:
        """The comment goes out through the pinned script action."""
        self.assertIn(GITHUB_SCRIPT, self.job)
        self.assertIn(COMMENT_MARKER, self.job)

    def test_the_comment_search_pages_through_every_comment(self) -> None:
        """The search reads every comment, not only the first page."""
        self.assertIn("github.paginate(github.rest.issues.listComments", self.job)

    def test_the_comment_search_only_takes_the_bot_comment(self) -> None:
        """A comment from another author never becomes the bot comment."""
        self.assertIn("github-actions[bot]", self.job)
        self.assertIn("comment.user?.login === botLogin", self.job)

    def test_the_comment_search_requires_the_marker_at_the_start(self) -> None:
        """A comment that only mentions the marker never becomes the bot comment."""
        self.assertIn("comment.body.startsWith(marker)", self.job)
        self.assertNotIn("comment.body.includes(marker)", self.job)

    def test_the_job_serializes_its_runs(self) -> None:
        """One comment job runs at a time for a pull request."""
        self.assertIn("group: coverage-comment-", self.job)
        self.assertIn("cancel-in-progress: true", self.job)

    def test_the_comment_search_removes_duplicate_comments(self) -> None:
        """A second coverage comment from the bot never survives."""
        self.assertIn("deleteComment", self.job)

    def test_the_comment_search_keeps_the_newest_result(self) -> None:
        """An older run never overwrites the result of a newer run."""
        self.assertIn("infiniteprep-coverage-run", self.job)
        self.assertIn("newestPosted > runId", self.job)

    def test_the_job_runs_only_on_a_pull_request(self) -> None:
        """The job stays out of the way on a push to main."""
        self.assertIn("github.event_name == 'pull_request'", self.job)

    def test_the_job_checks_out_nothing(self) -> None:
        """The job holds no checkout, so it keeps no credentials."""
        self.assertNotIn("actions/checkout@", self.job)

    def test_the_job_never_fails_the_pull_request(self) -> None:
        """A failure in the comment job leaves the pull request green."""
        self.assertIn("continue-on-error: true", self.job)


class CoverageDependencyTest(unittest.TestCase):
    """Keep the coverage tool pinned in the manifest and the lockfile."""

    def setUp(self) -> None:
        self.pyproject = PYPROJECT.read_text(encoding="utf-8")
        self.lockfile = LOCKFILE.read_text(encoding="utf-8")

    def test_the_dev_extra_pins_the_coverage_version(self) -> None:
        """The development extra holds the pinned coverage version."""
        self.assertIn(f'"coverage=={COVERAGE_VERSION}"', self.pyproject)

    def test_the_lockfile_pins_the_coverage_version(self) -> None:
        """The lockfile holds the pinned coverage version."""
        self.assertRegex(self.lockfile, rf"(?m)^coverage=={re.escape(COVERAGE_VERSION)}$")

    def test_the_settings_measure_the_source_package_with_branches(self) -> None:
        """The coverage settings measure the source package and the branches."""
        self.assertIn("[tool.coverage.run]", self.pyproject)
        self.assertIn('source = ["src/infiniteprep"]', self.pyproject)
        self.assertIn("branch = true", self.pyproject)


if __name__ == "__main__":
    unittest.main()
