#!/usr/bin/env python3
"""Cover the pull request comment of the security-devops workflow.

The wiring tests read the parsed workflow. The script tests run the comment
script from the workflow under Node against a stubbed API.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "security-devops.yml"

UPLOAD_ARTIFACT = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
DOWNLOAD_ARTIFACT = "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"
GITHUB_SCRIPT = "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3"
SUMMARY_ARTIFACT = "security-scan-summary"
SUMMARY_FILE = "security-scan-summary.json"
COMMENT_STEP = "Comment the scan result on the pull request"
BUILD_COMMAND = (
    f'python3 scripts/build_security_scan_summary.py --sarif "$SARIF_FILE" --output {SUMMARY_FILE}'
)
BOT = "github-actions[bot]"
MARKER = "<!-- infiniteprep-security-scan -->"
OTHER = "someone-else"
RUN_URL = "https://github.com/abuzucom/infiniteprep/actions/runs/100"
NODE = shutil.which("node")

HARNESS = """
const fs = require('fs');
const callsPath = process.env.STUB_CALLS;
function record(entry) {
  fs.appendFileSync(callsPath, JSON.stringify(entry) + '\\n');
}
process.on('unhandledRejection', (error) => {
  record({ action: 'error', message: error.message });
  process.exit(1);
});
const comments = JSON.parse(fs.readFileSync(process.env.STUB_COMMENTS, 'utf8'));
const github = {
  paginate: async () => comments,
  rest: {
    issues: {
      listComments: async () => ({ data: comments }),
      createComment: async (params) => {
        record({ action: 'create', body: params.body });
        return { data: { id: 1 } };
      },
      updateComment: async (params) => {
        record({ action: 'update', comment_id: params.comment_id, body: params.body });
        return { data: { id: params.comment_id } };
      },
      deleteComment: async (params) => {
        record({ action: 'delete', comment_id: params.comment_id });
        return { data: {} };
      },
    },
  },
};
const context = JSON.parse(fs.readFileSync(process.env.STUB_CONTEXT, 'utf8'));
const core = {
  info: (message) => record({ action: 'info', message }),
  setFailed: (message) => record({ action: 'failed', message }),
};
{
%(script)s
}
"""


def load_jobs() -> dict[str, Any]:
    """Return the parsed jobs of the security-devops workflow."""
    return dict(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"])


def find_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the step of one job with the given name."""
    for step in job["steps"]:
        if step.get("name") == name:
            return dict(step)
    raise AssertionError(f"missing step: {name}")


def bot_comment(comment_id: int, run_id: int) -> dict:
    """Return one bot comment that carries the marker."""
    return {
        "id": comment_id,
        "user": {"login": BOT},
        "body": f"{MARKER}\n<!-- infiniteprep-security-scan-run {run_id} -->\nold",
    }


def run_script(
    tmp_path: Path,
    summary: object,
    comments: list[dict],
    scan_result: str = "success",
    run_id: int = 100,
) -> list[dict]:
    """Run the workflow script and return the recorded API calls.

    A summary of None leaves the summary file absent, as after a failed scan.
    """
    if summary is not None:
        (tmp_path / SUMMARY_FILE).write_text(json.dumps(summary), encoding="utf-8")
    comments_path = tmp_path / "comments.json"
    comments_path.write_text(json.dumps(comments), encoding="utf-8")
    context_path = tmp_path / "context.json"
    stub_context = {
        "repo": {"owner": "abuzucom", "repo": "infiniteprep"},
        "issue": {"number": 1},
        "runId": run_id,
        "serverUrl": "https://github.com",
    }
    context_path.write_text(json.dumps(stub_context), encoding="utf-8")
    calls_path = tmp_path / "calls.json"
    calls_path.write_text("", encoding="utf-8")
    harness_path = tmp_path / "harness.js"
    script = find_step(load_jobs()["scan-comment"], COMMENT_STEP)["with"]["script"]
    harness_path.write_text(HARNESS % {"script": script}, encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "STUB_COMMENTS": str(comments_path),
            "STUB_CONTEXT": str(context_path),
            "STUB_CALLS": str(calls_path),
            "SCAN_RESULT": scan_result,
        }
    )
    completed = subprocess.run(
        [str(NODE), str(harness_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    lines = [line for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    calls = [json.loads(line) for line in lines]
    # A recorded error exits 1 through the harness. Any other exit code means node
    # itself failed, so the failure names its stderr instead of an empty call list.
    expected_exit = 1 if any(call["action"] == "error" for call in calls) else 0
    if completed.returncode != expected_exit:
        raise AssertionError(
            f"node exited {completed.returncode}, expected {expected_exit}: {completed.stderr}"
        )
    return calls


class ScanSummaryWiringTest(unittest.TestCase):
    """The scan job builds the summary with the tested script and holds no write to the PR."""

    def setUp(self) -> None:
        self.job = load_jobs()["msdo"]

    def test_the_summary_comes_from_the_tested_script(self) -> None:
        """The SARIF path reaches the builder as an environment value."""
        step = find_step(self.job, "Build the scan summary")
        self.assertEqual(step["run"], BUILD_COMMAND)
        self.assertEqual(step["env"], {"SARIF_FILE": "${{ steps.msdo.outputs.sarifFile }}"})

    def test_the_summary_artifact_is_uploaded_with_a_pinned_action(self) -> None:
        """The summary goes up through the pinned upload action."""
        step = find_step(self.job, "Upload the scan summary")
        self.assertEqual(step["uses"], UPLOAD_ARTIFACT)
        self.assertEqual(step["with"]["name"], SUMMARY_ARTIFACT)
        self.assertEqual(step["with"]["path"], SUMMARY_FILE)
        self.assertEqual(step["with"]["if-no-files-found"], "error")

    def test_the_scan_job_exports_the_light_flag(self) -> None:
        """The comment job reads the class of the change from the scan job."""
        self.assertEqual(self.job["outputs"], {"light": "${{ steps.classify.outputs.light }}"})

    def test_the_scan_job_cannot_write_to_the_pull_request(self) -> None:
        """The job that runs the scanners holds no pull request permission."""
        self.assertNotIn("pull-requests", self.job["permissions"])


class ScanCommentJobTest(unittest.TestCase):
    """The comment job stays separate and least privilege."""

    def setUp(self) -> None:
        self.job = load_jobs()["scan-comment"]
        self.script = find_step(self.job, COMMENT_STEP)["with"]["script"]

    def test_the_job_writes_only_the_pull_request_comment(self) -> None:
        """The job holds the pull request write permission and nothing else."""
        self.assertEqual(self.job["permissions"], {"contents": "read", "pull-requests": "write"})

    def test_the_job_follows_the_scan_job(self) -> None:
        """The job waits for the scan result."""
        self.assertEqual(self.job["needs"], "msdo")

    def test_the_job_runs_after_a_passed_and_a_failed_scan(self) -> None:
        """The status function replaces the implicit success check on needs."""
        self.assertIn("!cancelled()", self.job["if"])
        self.assertNotIn("needs.msdo.result", self.job["if"])

    def test_the_job_runs_only_on_a_pull_request(self) -> None:
        """The job stays out of the way on a push to main."""
        self.assertIn("github.event_name == 'pull_request'", self.job["if"])

    def test_the_job_skips_a_light_change(self) -> None:
        """A light change runs no scan, so no result exists to report."""
        self.assertIn("needs.msdo.outputs.light != 'true'", self.job["if"])

    def test_the_job_skips_a_pull_request_from_a_fork(self) -> None:
        """A pull request from a fork receives a read-only token."""
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.job["if"],
        )

    def test_the_job_serializes_its_runs(self) -> None:
        """One comment job runs at a time for a pull request."""
        group = self.job["concurrency"]["group"]
        self.assertEqual(group, "security-scan-comment-${{ github.event.pull_request.number }}")
        self.assertIs(self.job["concurrency"]["cancel-in-progress"], True)

    def test_the_job_downloads_the_summary_only_after_a_passed_scan(self) -> None:
        """A failed scan uploads no summary, so the download waits for a pass."""
        step = find_step(self.job, "Download the scan summary")
        self.assertEqual(step["uses"], DOWNLOAD_ARTIFACT)
        self.assertEqual(step["with"]["name"], SUMMARY_ARTIFACT)
        self.assertEqual(step["if"], "needs.msdo.result == 'success'")

    def test_the_job_posts_the_comment_with_a_pinned_action(self) -> None:
        """The comment goes out through the pinned script action."""
        step = find_step(self.job, COMMENT_STEP)
        self.assertEqual(step["uses"], GITHUB_SCRIPT)
        self.assertEqual(step["env"], {"SCAN_RESULT": "${{ needs.msdo.result }}"})

    def test_the_job_checks_out_nothing(self) -> None:
        """The job holds no checkout, so it keeps no credentials."""
        for step in self.job["steps"]:
            self.assertFalse(str(step.get("uses", "")).startswith("actions/checkout@"))

    def test_no_step_silences_a_failure(self) -> None:
        """The comment job runs as an ordinary job. No check is silenced."""
        self.assertNotIn("continue-on-error", self.job)
        for step in self.job["steps"]:
            self.assertNotIn("continue-on-error", step)

    def test_the_comment_search_pages_through_every_comment(self) -> None:
        """The search reads every comment, not only the first page."""
        self.assertIn("github.paginate(github.rest.issues.listComments", self.script)


@unittest.skipIf(NODE is None, "node is not installed")
class ScanCommentScriptTest(unittest.TestCase):
    """Exercise the comment script of the security-devops workflow."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_clean_scan_creates_the_comment(self) -> None:
        """A passed scan with no finding still posts a comment."""
        calls = run_script(self.tmp, {"total": 0, "tools": []}, [])

        self.assertEqual([call["action"] for call in calls], ["create"])
        body = calls[0]["body"]
        self.assertTrue(body.startswith(MARKER))
        self.assertIn("Result: pass", body)
        self.assertIn("Findings: 0", body)
        self.assertIn(RUN_URL, body)

    def test_the_comment_lists_the_findings_of_each_tool(self) -> None:
        """Every tool record becomes one row of the comment."""
        summary = {
            "total": 3,
            "tools": [{"name": "checkov", "findings": 3}, {"name": "bandit", "findings": 0}],
        }

        calls = run_script(self.tmp, summary, [])

        self.assertEqual([call["action"] for call in calls], ["create"])
        self.assertIn("Findings: 3", calls[0]["body"])
        self.assertIn("3  checkov", calls[0]["body"])
        self.assertIn("0  bandit", calls[0]["body"])

    def test_a_failed_scan_posts_the_failure_without_a_summary(self) -> None:
        """A failed scan job leaves no summary, and the comment reports the failure."""
        calls = run_script(self.tmp, None, [], scan_result="failure")

        self.assertEqual([call["action"] for call in calls], ["create"])
        self.assertIn("Result: failure", calls[0]["body"])
        self.assertIn(RUN_URL, calls[0]["body"])
        self.assertNotIn("Findings:", calls[0]["body"])

    def test_an_unknown_scan_result_is_rejected(self) -> None:
        """A job result outside the known values never reaches the comment."""
        calls = run_script(self.tmp, {"total": 0, "tools": []}, [], scan_result="skipped")

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("scan job result", calls[0]["message"])

    def test_a_passed_scan_without_a_summary_is_rejected(self) -> None:
        """A passed scan with no summary file fails the job."""
        calls = run_script(self.tmp, None, [])

        self.assertEqual([call["action"] for call in calls], ["error"])

    def test_a_foreign_comment_never_becomes_the_target(self) -> None:
        """A comment from another author that carries the marker is left alone."""
        foreign = {"id": 7, "user": {"login": OTHER}, "body": f"{MARKER}\nold"}

        calls = run_script(self.tmp, {"total": 0, "tools": []}, [foreign])

        self.assertEqual([call["action"] for call in calls], ["create"])

    def test_a_bot_comment_with_the_marker_inside_the_body_is_left_alone(self) -> None:
        """A bot comment that only mentions the marker is not the target."""
        mention = {"id": 8, "user": {"login": BOT}, "body": "text before the marker " + MARKER}

        calls = run_script(self.tmp, {"total": 0, "tools": []}, [mention])

        self.assertEqual([call["action"] for call in calls], ["create"])

    def test_a_second_bot_comment_is_removed(self) -> None:
        """A second scan comment from the bot does not survive."""
        comments = [bot_comment(11, 90), bot_comment(12, 90)]

        calls = run_script(self.tmp, {"total": 0, "tools": []}, comments)

        self.assertEqual([call["action"] for call in calls], ["update", "delete"])
        self.assertEqual(calls[0]["comment_id"], 11)
        self.assertEqual(calls[1]["comment_id"], 12)

    def test_an_older_run_never_replaces_a_newer_result(self) -> None:
        """A run older than the posted run writes nothing."""
        calls = run_script(self.tmp, {"total": 0, "tools": []}, [bot_comment(13, 500)])

        self.assertEqual([call["action"] for call in calls], ["info"])

    def test_a_tool_name_with_a_fence_character_is_rejected(self) -> None:
        """A crafted tool name never reaches the comment."""
        summary = {"total": 1, "tools": [{"name": "bandit`\n@someone", "findings": 1}]}

        calls = run_script(self.tmp, summary, [])

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("unsupported character", calls[0]["message"])

    def test_an_unexpected_field_is_rejected(self) -> None:
        """A summary with an extra field never reaches the comment."""
        calls = run_script(self.tmp, {"total": 0, "tools": [], "body": "injected"}, [])

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("unexpected fields", calls[0]["message"])

    def test_a_negative_total_is_rejected(self) -> None:
        """A total outside the range never reaches the comment."""
        calls = run_script(self.tmp, {"total": -1, "tools": []}, [])

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("out of range", calls[0]["message"])

    def test_too_many_tools_are_rejected(self) -> None:
        """A tool list longer than the cap never reaches the comment."""
        tools = [{"name": f"tool{index}", "findings": 0} for index in range(21)]

        calls = run_script(self.tmp, {"total": 0, "tools": tools}, [])

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("out of range", calls[0]["message"])

    def test_a_tool_record_with_an_extra_field_is_rejected(self) -> None:
        """A tool record with an extra field never reaches the comment."""
        summary = {"total": 0, "tools": [{"name": "bandit", "findings": 0, "note": "x"}]}

        calls = run_script(self.tmp, summary, [])

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("unexpected fields", calls[0]["message"])


if __name__ == "__main__":
    unittest.main()
