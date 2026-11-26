#!/usr/bin/env python3
"""Cover the agents-personas foucault and euler review wiring."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "ci.yml"
SECURITY_CALLER = WORKFLOWS / "security-review-pr.yml"
QUALITY_CALLER = WORKFLOWS / "quality-review-pr.yml"

PERSONAS_COMMIT = "c43f4592191b42b9f66072622b082fc2f4dc3217"
PERSONAS_WORKFLOWS = "abuzucom/agents-personas/.github/workflows/"
FOUCAULT_USES = PERSONAS_WORKFLOWS + "foucault-security-review.yml@" + PERSONAS_COMMIT
EULER_USES = PERSONAS_WORKFLOWS + "euler-quality-review.yml@" + PERSONAS_COMMIT
FOUCAULT_CHECK = "foucault / security"
EULER_CHECK = "euler / quality"
PROVIDER_SECRET_MAPPING = "MODEL_API_KEY: ${{ secrets.OLLAMA_API_KEY }}"
REVIEW_PERMISSIONS = ("checks: write", "contents: read", "pull-requests: write")
# The engine supplies the adapter. These copies left the repository.
RETIRED_ADAPTER_FILES = (
    "ci/build_pr_case.py",
    "ci/build_retry_case.py",
    "ci/call_model.py",
    "ci/model_providers.json",
    "ci/run_model_command.py",
    "scripts/check_pr_review_response.py",
)
RETIRED_REPOSITORIES = ("abuzucom/foucault", "abuzucom/euler")

USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.MULTILINE)
FULL_SHA_REF = re.compile(r"[^@\s]+@[0-9a-f]{40}")
JOB_HEADER = re.compile(r"^  [A-Za-z0-9_-]+:\n", re.MULTILINE)


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
    remainder = workflow_text[header_match.end():]
    next_job = re.search(r"^  \S[^\n]*:\n", remainder, re.MULTILINE)
    return remainder if next_job is None else remainder[:next_job.start()]


def list_job_names(workflow_text: str) -> list[str]:
    """Return the top-level job names below the jobs key."""
    jobs_text = workflow_text[workflow_text.index("\njobs:\n"):]
    return [header.strip().rstrip(":") for header in JOB_HEADER.findall(jobs_text)]


class CiWorkflowTest(unittest.TestCase):
    """Keep the ci workflow that both review callers trigger on."""

    def test_ci_name_matches_review_triggers(self) -> None:
        text = read_workflow_text(CI_WORKFLOW)
        self.assertIn("\nname: ci\n", text)
        self.assertIn("  pull_request:", text)

    def test_ci_checkout_never_persists_credentials(self) -> None:
        text = read_workflow_text(CI_WORKFLOW)
        self.assertEqual(
            text.count("actions/checkout@"),
            text.count("persist-credentials: false"),
        )

    def test_lint_tools_use_lockfile_constraints(self) -> None:
        workflow = read_workflow_text(CI_WORKFLOW)
        command = "python -m pip install --require-hashes --constraint requirements-lock.txt ruff pytest"
        self.assertTrue(command in workflow, "CI lint tools must use lockfile constraints")
        lockfile = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8")
        self.assertRegex(lockfile, r"(?m)^ruff==[0-9.]+ \\?$")
        self.assertRegex(lockfile, r"(?m)^pytest==[0-9.]+ \\?$")


class SecurityReviewWiringTest(unittest.TestCase):
    """Keep the foucault caller pinned, scoped, and complete."""

    def setUp(self) -> None:
        self.text = read_workflow_text(SECURITY_CALLER)

    def test_triggers_after_ci(self) -> None:
        self.assertIn("workflow_run:", self.text)
        self.assertIn("workflows: [ci]", self.text)
        self.assertIn("types: [completed]", self.text)
        self.assertIn("branches-ignore:\n      - main", self.text)

    def test_review_pins_one_personas_commit(self) -> None:
        review_job = extract_job_block(self.text, "review")
        self.assertIn(f"uses: {FOUCAULT_USES}\n", review_job)
        self.assertIn(f'personas_ref: "{PERSONAS_COMMIT}"', review_job)
        self.assertIn("fail_on_block: true", review_job)
        self.assertIn("pr_comment: true", review_job)

    def test_review_job_grants_wrapper_permissions(self) -> None:
        review_job = extract_job_block(self.text, "review")
        for permission in REVIEW_PERMISSIONS:
            self.assertIn(permission, review_job)

    def test_only_review_maps_secret(self) -> None:
        self.assertEqual(self.text.count("secrets:"), 1)
        self.assertIn(PROVIDER_SECRET_MAPPING, extract_job_block(self.text, "review"))
        self.assertNotIn("secrets: inherit", self.text)

    def test_engine_owns_fork_handling(self) -> None:
        job_names = list_job_names(self.text)
        self.assertNotIn("fork-review", job_names)
        self.assertNotIn("fork-review-skipped", job_names)
        self.assertNotIn("fork_review:", self.text)

    def test_checks_use_persona_name(self) -> None:
        self.assertIn(f'check_name: "{FOUCAULT_CHECK}"', self.text)
        skip_job = extract_job_block(self.text, "docs-only-skipped")
        self.assertIn(f"name: '{FOUCAULT_CHECK}'", skip_job)
        self.assertNotIn("security-review'", self.text)

    def test_prefilter_reads_verdict_title(self) -> None:
        self.assertIn('run.app?.slug === "github-actions"', self.text)
        self.assertIn("run.output?.title", self.text)


class QualityReviewWiringTest(unittest.TestCase):
    """Keep the euler caller pinned, scoped, and complete."""

    def setUp(self) -> None:
        self.text = read_workflow_text(QUALITY_CALLER)

    def test_triggers_after_ci(self) -> None:
        self.assertIn("workflow_run:", self.text)
        self.assertIn("workflows: [ci]", self.text)
        self.assertIn("types: [completed]", self.text)
        self.assertIn("branches-ignore:\n      - main", self.text)

    def test_review_pins_one_personas_commit(self) -> None:
        review_job = extract_job_block(self.text, "review")
        self.assertIn(f"uses: {EULER_USES}\n", review_job)
        self.assertIn(f'personas_ref: "{PERSONAS_COMMIT}"', review_job)
        self.assertIn("fail_on_block: true", review_job)
        self.assertNotIn("pr_title:", review_job)
        self.assertNotIn("pr_body:", review_job)
        self.assertNotIn("fromJSON", review_job)

    def test_review_job_grants_wrapper_permissions(self) -> None:
        review_job = extract_job_block(self.text, "review")
        for permission in REVIEW_PERMISSIONS:
            self.assertIn(permission, review_job)

    def test_only_review_maps_secret(self) -> None:
        self.assertEqual(self.text.count("secrets:"), 1)
        self.assertIn(PROVIDER_SECRET_MAPPING, extract_job_block(self.text, "review"))
        self.assertNotIn("secrets: inherit", self.text)

    def test_engine_owns_fork_handling(self) -> None:
        self.assertNotIn("fork-skip", list_job_names(self.text))
        self.assertNotIn("fork_review:", self.text)

    def test_checks_use_persona_name(self) -> None:
        self.assertIn(f"check_name: '{EULER_CHECK}'", self.text)
        skip_job = extract_job_block(self.text, "docs-only-skipped")
        self.assertIn(f"name: '{EULER_CHECK}'", skip_job)
        self.assertNotIn("quality-review'", self.text)


class RetiredReviewerTest(unittest.TestCase):
    """Keep the standalone reviewer pins and adapter copies out."""

    def test_adapter_copies_are_gone(self) -> None:
        for relative_path in RETIRED_ADAPTER_FILES:
            with self.subTest(path=relative_path):
                self.assertFalse((ROOT / relative_path).exists())

    def test_callers_name_no_retired_repository(self) -> None:
        for workflow_path in (SECURITY_CALLER, QUALITY_CALLER):
            text = read_workflow_text(workflow_path)
            for repository in RETIRED_REPOSITORIES:
                with self.subTest(workflow=workflow_path.name, repository=repository):
                    self.assertNotIn(repository + "/", text)
            self.assertNotIn("ci/", text)


class ActionPinTest(unittest.TestCase):
    """Pin every action and reusable workflow to a full commit SHA."""

    def test_uses_references_are_full_shas(self) -> None:
        for workflow_path in (CI_WORKFLOW, SECURITY_CALLER, QUALITY_CALLER):
            text = read_workflow_text(workflow_path)
            references = USES_LINE.findall(text)
            self.assertTrue(references, workflow_path.name)
            for reference in references:
                self.assertTrue(
                    FULL_SHA_REF.fullmatch(reference),
                    f"{workflow_path.name}: {reference}",
                )


if __name__ == "__main__":
    unittest.main()
