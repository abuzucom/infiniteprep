#!/usr/bin/env python3
"""Cover the foucault security review and euler quality review wiring."""
import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "ci.yml"
SECURITY_CALLER = WORKFLOWS / "security-review-pr.yml"
QUALITY_CALLER = WORKFLOWS / "quality-review-pr.yml"

FOUCAULT_COMMIT = "c398b5a29890ba01dee5065a7dc97a91e3e2b08b"
EULER_COMMIT = "1a3f67cba9d6ed7f45af4822f9ca27dd14db6a46"
FOUCAULT_USES = (
    "abuzucom/foucault/.github/workflows/security-review.yml@" + FOUCAULT_COMMIT
)
EULER_USES = "abuzucom/euler/.github/workflows/quality-review.yml@" + EULER_COMMIT
PROVIDER_SECRET_MAPPING = "MODEL_API_KEY: ${{ secrets.OLLAMA_API_KEY }}"

# SHA-256 digests of the adapter files at FOUCAULT_COMMIT. The reusable
# security workflow runs these from this repository's checkout.
FOUCAULT_ADAPTER_DIGESTS = {
    "ci/build_pr_case.py":
        "9488d5448be7fcd70ceba5a7af7a5b7897a0b1d9dac842aa301ada8995afb019",
    "ci/call_model.py":
        "f6c9c3baa5d32e07c510c94713a89cd92fca144374465f8bbd0b11b5dc708001",
    "ci/model_providers.json":
        "9544f1a5cd6602cb1114b482b200fd286cdfcdb17a918034499534e6e124db06",
    "ci/run_model_command.py":
        "494cb0bfd49b6d490c87f009a0b252cb988c2fa74c1a6efe11435e72173899e5",
    "scripts/check_pr_review_response.py":
        "699716c81f718c39f4afccb9139f84de2e7c26fde5cf030a009f1dd6abbeec90",
}

USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.MULTILINE)
FULL_SHA_REF = re.compile(r"[^@\s]+@[0-9a-f]{40}")


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


class CiWorkflowTest(unittest.TestCase):
    """Keep the ci workflow that both review callers trigger on."""

    def test_ci_workflow_name_matches_the_review_triggers(self) -> None:
        text = read_workflow_text(CI_WORKFLOW)
        self.assertIn("\nname: ci\n", text)
        self.assertIn("  pull_request:", text)

    def test_ci_checkout_does_not_persist_credentials(self) -> None:
        text = read_workflow_text(CI_WORKFLOW)
        self.assertEqual(
            text.count("actions/checkout@"),
            text.count("persist-credentials: false"),
        )


class SecurityReviewWiringTest(unittest.TestCase):
    """Keep the foucault caller pinned, scoped, and complete."""

    def setUp(self) -> None:
        self.text = read_workflow_text(SECURITY_CALLER)

    def test_triggers_after_ci(self) -> None:
        self.assertIn("workflow_run:", self.text)
        self.assertIn("workflows: [ci]", self.text)
        self.assertIn("types: [completed]", self.text)
        self.assertIn('workflowRun?.name === "ci"', self.text)

    def test_review_jobs_pin_workflow_and_policy_to_one_commit(self) -> None:
        for job_name in ("review", "fork-review"):
            job_text = extract_job_block(self.text, job_name)
            self.assertIn(f"uses: {FOUCAULT_USES}\n", job_text)
            self.assertIn(f'audit_ref: "{FOUCAULT_COMMIT}"', job_text)
            self.assertIn("fail_on_block: true", job_text)

    def test_only_the_same_repository_job_maps_the_provider_secret(self) -> None:
        review_job = extract_job_block(self.text, "review")
        self.assertIn(PROVIDER_SECRET_MAPPING, review_job)
        self.assertIn("fork_review: false", review_job)
        fork_job = extract_job_block(self.text, "fork-review")
        self.assertNotIn("secrets:", fork_job)
        self.assertIn("fork_review: true", fork_job)
        self.assertIn("safe-to-review", self.text)
        self.assertNotIn("secrets: inherit", self.text)

    def test_unapproved_forks_skip_without_a_secret(self) -> None:
        skip_job = extract_job_block(self.text, "fork-review-skipped")
        self.assertIn("same_repository == 'false'", skip_job)
        self.assertNotIn("secrets", skip_job)

    def test_adapter_files_match_the_pinned_foucault_commit(self) -> None:
        self.assertIn('model_call_command: "python3 ci/call_model.py"', self.text)
        for relative_path, expected_digest in FOUCAULT_ADAPTER_DIGESTS.items():
            adapter_path = ROOT / relative_path
            self.assertTrue(adapter_path.is_file(), f"missing {relative_path}")
            actual_digest = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
            self.assertEqual(actual_digest, expected_digest, relative_path)


class QualityReviewWiringTest(unittest.TestCase):
    """Keep the euler caller pinned, scoped, and complete."""

    def setUp(self) -> None:
        self.text = read_workflow_text(QUALITY_CALLER)

    def test_triggers_after_ci(self) -> None:
        self.assertIn("workflow_run:", self.text)
        self.assertIn("workflows: [ci]", self.text)
        self.assertIn("types: [completed]", self.text)

    def test_review_job_pins_workflow_and_policy_to_one_commit(self) -> None:
        review_job = extract_job_block(self.text, "review")
        self.assertIn(f"uses: {EULER_USES}\n", review_job)
        self.assertIn(f'quality_ref: "{EULER_COMMIT}"', review_job)
        self.assertIn("fail_on_block: true", review_job)

    def test_review_job_grants_the_reusable_job_permissions(self) -> None:
        review_job = extract_job_block(self.text, "review")
        for permission in (
            "contents: read",
            "actions: read",
            "pull-requests: write",
            "checks: write",
        ):
            self.assertIn(permission, review_job)

    def test_only_same_repository_reviews_receive_the_secret(self) -> None:
        review_job = extract_job_block(self.text, "review")
        self.assertIn(PROVIDER_SECRET_MAPPING, review_job)
        self.assertIn("same_repo == 'true'", review_job)
        fork_job = extract_job_block(self.text, "fork-skip")
        self.assertIn("same_repo != 'true'", fork_job)
        self.assertNotIn("secrets", fork_job)
        self.assertNotIn("secrets: inherit", self.text)


class ActionPinTest(unittest.TestCase):
    """Pin every action and reusable workflow to a full commit SHA."""

    def test_every_uses_reference_is_a_full_commit_sha(self) -> None:
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
