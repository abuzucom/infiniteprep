"""Behavioral checks for the review skip on docs-only pull requests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"
CLASSIFIER = REPOSITORY_ROOT / "scripts" / "classify_changed_paths.py"
GIT = shutil.which("git") or "git"
GIT_IDENTITY = ("-c", "user.name=Test Author", "-c", "user.email=author@example.invalid")
CLASSIFY_COMMAND = 'python3 scripts/classify_changed_paths.py --base "$BASE_SHA" --head "$HEAD_SHA"'
TRUSTED_PATH = "trusted-base"
# The workflow expression that hands the run's own access grant to the fetch.
RUN_ACCESS_EXPRESSION = "${{ github.token }}"
SHA_GUARD = '[[ "$HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]'
FETCH_COMMAND = 'fetch --no-tags origin "$HEAD_SHA"'
PULL_REF = "refs/pull/1/head"
SECURITY_CLASSIFY_GUARDS = (
    "needs.resolve-pr.result == 'success'",
    "needs.resolve-pr.outputs.already_reviewed != 'true'",
    "needs.resolve-pr.outputs.same_repository == 'true'",
    "needs.resolve-pr.outputs.fork_review_approved == 'true'",
    "needs.resolve-pr.outputs.compliance_passed == 'true'",
)
CLASS_OUTPUT = "${{ steps.classify.outputs.change_class }}"
NOT_CANCELLED = "!cancelled()"
NOT_DOCS = "needs.classify.outputs.change_class != 'docs'"
IS_DOCS = "needs.classify.outputs.change_class == 'docs'"
SKIPPED_CONCLUSION = "conclusion: 'skipped'"
SKIP_JOB = "docs-only-skipped"
CHECKOUT_ACTION = "actions/checkout@"


@dataclass(frozen=True)
class ReviewCaller:
    """One review caller workflow and the names its jobs use."""

    workflow: str
    resolve_job: str
    review_jobs: tuple[str, ...]
    check_name: str
    resolved_condition: str


CALLERS = (
    ReviewCaller(
        workflow="security-review-pr.yml",
        resolve_job="resolve-pr",
        review_jobs=("review", "fork-review"),
        check_name="security-review",
        resolved_condition="needs.resolve-pr.result == 'success'",
    ),
    ReviewCaller(
        workflow="quality-review-pr.yml",
        resolve_job="resolve",
        review_jobs=("review",),
        check_name="quality-review",
        resolved_condition="needs.resolve.outputs.found == 'true'",
    ),
)


def run_git(directory: Path, *arguments: str) -> str:
    """Run one Git command in a directory and return its output."""
    result = subprocess.run(
        [GIT, "-C", str(directory), *GIT_IDENTITY, "-c", "commit.gpgsign=false", *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def commit_file(repository: Path, name: str, text: str) -> str:
    """Write one file, commit it, and return the commit revision."""
    target = repository / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    run_git(repository, "add", "--all")
    run_git(repository, "commit", "--quiet", "--message", "change")
    return run_git(repository, "rev-parse", "HEAD")


def load_jobs(caller: ReviewCaller) -> dict[str, Any]:
    """Return the parsed jobs of one review caller."""
    text = (WORKFLOWS / caller.workflow).read_text(encoding="utf-8")
    return dict(yaml.safe_load(text)["jobs"])


def list_needs(job: dict[str, Any]) -> list[str]:
    """Return the needs of a job as a list."""
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


class ClassifyJobTest(unittest.TestCase):
    """The class comes from default-branch code, and the pull request is data."""

    def test_classify_job_holds_a_read_only_token_and_no_secret(self) -> None:
        """The job that reads pull request objects cannot write and sees no secret."""
        for caller in CALLERS:
            with self.subTest(workflow=caller.workflow):
                job = load_jobs(caller)["classify"]
                self.assertEqual(job["permissions"], {"contents": "read"})
                self.assertNotIn("secrets", job)
                self.assertEqual(list_needs(job), [caller.resolve_job])
                self.assertEqual(job["outputs"], {"change_class": CLASS_OUTPUT})

    def test_trusted_checkout_takes_the_default_branch(self) -> None:
        """The first checkout names no ref, so a workflow_run job gets the default branch."""
        for caller in CALLERS:
            with self.subTest(workflow=caller.workflow):
                trusted = load_jobs(caller)["classify"]["steps"][0]
                self.assertTrue(trusted["uses"].startswith(CHECKOUT_ACTION))
                self.assertEqual(trusted["with"]["path"], TRUSTED_PATH)
                self.assertNotIn("ref", trusted["with"])
                self.assertNotIn("repository", trusted["with"])
                self.assertEqual(trusted["with"]["fetch-depth"], 0)
                self.assertIs(trusted["with"]["persist-credentials"], False)

    def test_no_pull_request_checkout_exists(self) -> None:
        """The trusted checkout is the only checkout, so no pull request file lands on disk."""
        for caller in CALLERS:
            with self.subTest(workflow=caller.workflow):
                steps = load_jobs(caller)["classify"]["steps"]
                checkouts = [s for s in steps if s.get("uses", "").startswith(CHECKOUT_ACTION)]
                self.assertEqual(len(checkouts), 1)

    def test_fetch_takes_one_validated_commit_into_the_trusted_checkout(self) -> None:
        """The head commit arrives as Git objects, and only this step sees the token."""
        for caller in CALLERS:
            with self.subTest(workflow=caller.workflow):
                fetch = load_jobs(caller)["classify"]["steps"][1]
                self.assertEqual(fetch["working-directory"], TRUSTED_PATH)
                self.assertEqual(fetch["env"]["GH_TOKEN"], RUN_ACCESS_EXPRESSION)
                head_source = f"needs.{caller.resolve_job}.outputs.head_sha"
                self.assertIn(head_source, fetch["env"]["HEAD_SHA"])
                self.assertIn(SHA_GUARD, fetch["run"])
                self.assertIn(FETCH_COMMAND, fetch["run"])
                self.assertLess(fetch["run"].index(SHA_GUARD), fetch["run"].index(FETCH_COMMAND))

    def test_only_the_trusted_classifier_runs(self) -> None:
        """The classifier runs from the trusted checkout and receives no token."""
        for caller in CALLERS:
            with self.subTest(workflow=caller.workflow):
                steps = load_jobs(caller)["classify"]["steps"]
                self.assertEqual(len([step for step in steps if "run" in step]), 2)
                classify = steps[-1]
                self.assertEqual(classify["id"], "classify")
                self.assertEqual(classify["run"], CLASSIFY_COMMAND)
                self.assertEqual(classify["working-directory"], TRUSTED_PATH)
                self.assertEqual(set(classify["env"]), {"BASE_SHA", "HEAD_SHA"})
                self.assertNotIn("continue-on-error", classify)

    def test_unapproved_forks_get_no_classification(self) -> None:
        """The security caller classifies only when a review job would run."""
        condition = load_jobs(CALLERS[0])["classify"]["if"]
        for fragment in SECURITY_CLASSIFY_GUARDS:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, condition)


class ReviewSkipTest(unittest.TestCase):
    """A docs-only pull request skips the model review and still reports a check."""

    def test_review_jobs_run_unless_the_class_is_docs(self) -> None:
        """A failed classify job yields no class, and the review runs."""
        for caller in CALLERS:
            jobs = load_jobs(caller)
            for job_name in caller.review_jobs:
                with self.subTest(workflow=caller.workflow, job=job_name):
                    job = jobs[job_name]
                    self.assertIn("classify", list_needs(job))
                    self.assertIn(caller.resolve_job, list_needs(job))
                    self.assertIn(NOT_CANCELLED, job["if"])
                    self.assertIn(NOT_DOCS, job["if"])
                    self.assertIn(caller.resolved_condition, job["if"])

    def test_skip_job_runs_for_the_docs_class_only(self) -> None:
        """Website and mixed changes keep both reviews."""
        for caller in CALLERS:
            with self.subTest(workflow=caller.workflow):
                job = load_jobs(caller)[SKIP_JOB]
                self.assertEqual(job["if"], IS_DOCS)
                self.assertEqual(job["permissions"], {"checks": "write"})
                self.assertNotIn("secrets", job)

    def test_skip_job_publishes_the_review_check_as_skipped(self) -> None:
        """The check keeps its name, so a required check still reports."""
        for caller in CALLERS:
            with self.subTest(workflow=caller.workflow):
                steps = load_jobs(caller)[SKIP_JOB]["steps"]
                self.assertEqual(len(steps), 1)
                script = steps[0]["with"]["script"]
                self.assertIn(f"name: '{caller.check_name}'", script)
                self.assertIn(SKIPPED_CONCLUSION, script)
                self.assertIn("head_sha: process.env.HEAD_SHA", script)


class FetchedCommitClassificationTest(unittest.TestCase):
    """The trusted classifier reads a pull request commit fetched by its hash."""

    def setUp(self) -> None:
        """Create an origin, a trusted clone, and a contributor clone of it."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        origin = root / "origin"
        origin.mkdir()
        run_git(origin, "init", "--quiet")
        self.base_sha = commit_file(origin, "src/app.py", "VALUE = 1\n")
        self.trusted = root / TRUSTED_PATH
        self.contributor = root / "contributor"
        run_git(root, "clone", "--quiet", str(origin), str(self.trusted))
        run_git(root, "clone", "--quiet", str(origin), str(self.contributor))

    def publish_head(self, *files: tuple[str, str]) -> str:
        """Commit the files, push them to the pull request ref, and return the head."""
        head_sha = ""
        for name, text in files:
            head_sha = commit_file(self.contributor, name, text)
        run_git(self.contributor, "push", "--quiet", "origin", f"HEAD:{PULL_REF}")
        return head_sha

    def classify(self, head_sha: str) -> str:
        """Fetch the head commit into the trusted clone, classify, and return the class line."""
        run_git(self.trusted, "fetch", "--quiet", "--no-tags", "origin", head_sha)
        environment = dict(os.environ)
        environment.pop("GITHUB_OUTPUT", None)
        result = subprocess.run(
            [sys.executable, str(CLASSIFIER), "--base", self.base_sha, "--head", head_sha],
            cwd=self.trusted,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.splitlines()[0]

    def test_fetched_docs_commit_classifies_as_docs(self) -> None:
        """Only the commit objects arrive, and the working tree stays on the base."""
        head_sha = self.publish_head(("docs/guide.md", "Guide\n"))
        self.assertEqual(self.classify(head_sha), "change_class=docs")
        self.assertFalse((self.trusted / "docs" / "guide.md").exists())

    def test_fetched_website_commit_keeps_the_reviews(self) -> None:
        """A landing site change is not in the docs class."""
        head_sha = self.publish_head(("website/index.html", "<p>Site</p>\n"))
        self.assertEqual(self.classify(head_sha), "change_class=website")

    def test_fetched_code_commit_classifies_as_full(self) -> None:
        """A code change beside a docs change keeps both reviews."""
        head_sha = self.publish_head(("docs/guide.md", "Guide\n"), ("src/app.py", "VALUE = 2\n"))
        self.assertEqual(self.classify(head_sha), "change_class=full")


if __name__ == "__main__":
    unittest.main()
